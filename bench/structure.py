"""Structure benchmark: run the real pipeline on images/ and compare blocks with ground_truth.json.

Reports, per image, how many expected lines appear verbatim as a block with the expected kind
(heading for ALL-CAPS lines, bullet for the rest on slide photos, paragraph on diagram slides).

Usage: .venv/bin/python bench/structure.py [--show]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bench.benchmark import levenshtein, norm_text
from ss_to_word.images import load_image
from ss_to_word.layout import build_blocks
from ss_to_word.ocr import Engine

ROOT = Path(__file__).resolve().parent.parent
GT = json.loads((ROOT / "bench" / "ground_truth.json").read_text(encoding="utf-8"))


def expected_kind(line: str, diagram: bool) -> str:
    letters = [c for c in line if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return "heading"
    return "paragraph" if diagram else "bullet"


def main() -> None:
    show = "--show" in sys.argv
    engine = Engine(prefer_tesseract="--rapid" not in sys.argv)
    print("engine:", engine.info.describe())
    files = sorted((ROOT / "images").glob("*.jpg"))
    tot_lines = tot_text_ok = tot_kind_ok = 0
    cers = []
    t0 = time.time()
    for f in files:
        gt = GT[f.name]
        image = load_image(f)
        read = engine.read_page(image)
        blocks = build_blocks(read.lines, np.asarray(read.image),
                              tesseract=engine.tesseract is not None)
        by_text = {}
        for b in blocks:
            by_text.setdefault(norm_text(b.text), []).append(b.kind)
        text_ok = kind_ok = 0
        misses = []
        for gl in gt["lines"]:
            key = norm_text(gl)
            kinds = by_text.get(key)
            if kinds:
                text_ok += 1
                if expected_kind(gl, gt.get("diagram", False)) in kinds:
                    kind_ok += 1
                else:
                    misses.append(f"kind {kinds} != {expected_kind(gl, gt.get('diagram', False))}: {gl}")
            else:
                misses.append(f"missing: {gl}")
        if not gt.get("diagram"):
            ocr_text = norm_text("\n".join(b.text for b in blocks))
            gt_text = norm_text("\n".join(gt["lines"]))
            cers.append(levenshtein(ocr_text, gt_text) / max(1, len(gt_text)))
        extra = len(blocks) - text_ok
        tot_lines += len(gt["lines"])
        tot_text_ok += text_ok
        tot_kind_ok += kind_ok
        print(f"{f.name}  blocks={len(blocks):2d}  exact={text_ok:2d}/{len(gt['lines']):2d}  kind_ok={kind_ok:2d}  extra_blocks={extra}")
        for m in misses:
            print("     ", m)
        if show:
            for b in blocks:
                print(f"        [{b.kind[:4]}] {b.text}")
    print(f"\nTOTAL exact paragraphs: {tot_text_ok}/{tot_lines} ({tot_text_ok/tot_lines:.1%}),"
          f" with correct kind: {tot_kind_ok}/{tot_lines} ({tot_kind_ok/tot_lines:.1%}),"
          f" CER (slides): {sum(cers)/len(cers):.2%}, {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
