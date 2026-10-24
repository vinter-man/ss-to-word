"""Compare OCR engines / preprocessing variants against bench/ground_truth.json.

Usage:  .venv/bin/python bench/benchmark.py [engine ...]
Engines are registered in ENGINES below. Raw outputs are written to bench/out/<engine>/.
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "images"
GT = json.loads((ROOT / "bench" / "ground_truth.json").read_text(encoding="utf-8"))
OUT = ROOT / "bench" / "out"

BULLET_RE = re.compile(r"^[\s•●○◦▪■□▫\-–—*»«>✓✔√vVеe°·.,:;|_]{1,3}\s+")


def load_gray(path: Path, scale: float = 2.0) -> Image.Image:
    im = ImageOps.exif_transpose(Image.open(path)).convert("L")
    if scale != 1:
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    return im


# ---------------------------------------------------------------- engines
def tesseract_factory(psm: int = 3, scale: float = 2.0, extra: str = "", lang: str = "bul"):
    """Plain Tesseract on the whole photo (needs `pip install pytesseract`)."""
    import pytesseract

    def run(path: Path) -> str:
        im = load_gray(path, scale)
        return pytesseract.image_to_string(im, lang=lang, config=f"--oem 1 --psm {psm} {extra}".strip())

    return run


def easyocr_factory():
    import easyocr

    reader = easyocr.Reader(["bg"], gpu=False, verbose=False)

    def run(path: Path) -> str:
        res = reader.readtext(str(path), detail=1, paragraph=False)
        res.sort(key=lambda r: (min(p[1] for p in r[0]), min(p[0] for p in r[0])))
        return "\n".join(r[1] for r in res)

    return run


def rapidocr_factory():
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion

    eng = RapidOCR(params={"Rec.lang_type": LangRec.CYRILLIC, "Rec.ocr_version": OCRVersion.PPOCRV5,
                           "Rec.model_type": ModelType.MOBILE})

    def run(path: Path) -> str:
        res = eng(str(path))
        if not res.txts:
            return ""
        items = list(zip(res.boxes, res.txts, res.scores, strict=True))
        items.sort(key=lambda r: (min(p[1] for p in r[0]), min(p[0] for p in r[0])))
        return "\n".join(t for _, t, s in items if s >= 0.6)

    return run


def app_factory(prefer_tesseract: bool = True):
    """The pipeline this app actually ships: RapidOCR detection + per-line recognition."""
    import sys

    sys.path.insert(0, str(ROOT))
    from ss_to_word.images import load_image
    from ss_to_word.ocr import Engine

    engine = Engine(prefer_tesseract=prefer_tesseract)

    def run(path: Path) -> str:
        lines = engine.read(load_image(path))
        return "\n".join(ln.text for ln in lines if ln.text)

    return run


ENGINES = {
    "tess_psm3_x2": lambda: tesseract_factory(3, 2.0),
    "tess_psm3_x1": lambda: tesseract_factory(3, 1.0),
    "tess_psm3_x3": lambda: tesseract_factory(3, 3.0),
    "tess_psm4_x2": lambda: tesseract_factory(4, 2.0),
    "tess_psm6_x2": lambda: tesseract_factory(6, 2.0),
    "tess_psm3_x2_sauvola": lambda: tesseract_factory(3, 2.0, "-c thresholding_method=2"),
    "tess_psm3_x2_buleng": lambda: tesseract_factory(3, 2.0, lang="bul+eng"),
    "easyocr": easyocr_factory,
    "rapidocr": rapidocr_factory,
    "app": app_factory,                                      # what the program uses
    "app_no_tesseract": lambda: app_factory(prefer_tesseract=False),
}


# ---------------------------------------------------------------- metrics
def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = s.replace("„", '"').replace("“", '"').replace("”", '"').replace("«", '"').replace("»", '"')
    lines = []
    for ln in s.splitlines():
        ln = BULLET_RE.sub("", ln.strip())
        if ln:
            lines.append(ln)
    return re.sub(r"\s+", " ", " ".join(lines)).strip().casefold()


def words(s: str) -> list[str]:
    return [w for w in (t.strip("\"'.,:;!?()[]{}•·-–—") for t in norm_text(s).split()) if w]


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return len(a) + len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def score(ocr: str, entry: dict) -> dict:
    gt_text = norm_text("\n".join(entry["lines"]))
    ocr_text = norm_text(ocr)
    gw, ow = Counter(words("\n".join(entry["lines"]))), Counter(words(ocr))
    optional = Counter(w.casefold() for w in entry.get("optional", []))
    matched = sum((gw & ow).values())
    noise = sum(((ow - gw) - optional).values())
    return {
        "cer": None if entry.get("diagram") else levenshtein(ocr_text, gt_text) / max(1, len(gt_text)),
        "recall": matched / max(1, sum(gw.values())),
        "precision": matched / max(1, matched + noise),
        "noise": noise,
    }


def main(argv: list[str]) -> None:
    names = argv or ["tess_psm3_x2"]
    files = sorted(IMAGES.glob("*.jpg"))
    for name in names:
        run = ENGINES[name]()
        outdir = OUT / name
        outdir.mkdir(parents=True, exist_ok=True)
        rows, t0 = [], time.time()
        for f in files:
            txt = run(f)
            (outdir / (f.stem + ".txt")).write_text(txt, encoding="utf-8")
            rows.append((f.name, score(txt, GT[f.name])))
        dt = time.time() - t0
        cers = [r["cer"] for _, r in rows if r["cer"] is not None]
        print(f"\n=== {name}  ({dt:.1f}s, {dt/len(files):.1f}s/img) ===")
        print(f"  mean CER (bullet slides): {sum(cers)/len(cers):.3%}   "
              f"mean recall: {sum(r['recall'] for _, r in rows)/len(rows):.3%}   "
              f"mean precision: {sum(r['precision'] for _, r in rows)/len(rows):.3%}   "
              f"noise words: {sum(r['noise'] for _, r in rows)}")
        for fn, r in rows:
            cer = "  diag " if r["cer"] is None else f"{r['cer']:6.1%}"
            print(f"  {fn[-12:]}  cer={cer}  rec={r['recall']:6.1%}  prec={r['precision']:6.1%}  noise={r['noise']}")


if __name__ == "__main__":
    main(sys.argv[1:])
