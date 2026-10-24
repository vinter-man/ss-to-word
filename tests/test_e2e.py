"""End-to-end tests: synthetic slide images -> real OCR engine -> docx/txt.

They need the OCR models (bundled) and, for the best-accuracy path, Tesseract. Both engine
paths are exercised when Tesseract is available.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from docx import Document

from ss_to_word.images import HEIF_SUPPORTED
from ss_to_word.ocr import Engine
from ss_to_word.ocr.tesseract import find_tesseract
from ss_to_word.pipeline import Job, run_job
from tests.conftest import SLIDE_A, SLIDE_B, SLIDE_C, cyrillic_font_path, render_slide


@pytest.fixture(scope="module")
def sample_folder(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("photos")
    fp = cyrillic_font_path()
    # names deliberately out of lexical order to check natural sorting: 10 > 2 > 1
    render_slide(SLIDE_A, font_path=fp).save(folder / "IMG_1.png")
    render_slide(SLIDE_B, font_path=fp, dark=True, tilt_deg=2.0).save(folder / "IMG_2.jpg", quality=88)
    img_c = render_slide(SLIDE_C, font_path=fp)
    if HEIF_SUPPORTED:
        img_c.save(folder / "IMG_10.HEIC", format="HEIF", quality=90)
    else:
        img_c.save(folder / "IMG_10.jpeg", quality=90)
    (folder / "notes.txt").write_text("not an image", encoding="utf-8")
    return folder


def expected_texts() -> list[str]:
    return [t for slide in (SLIDE_A, SLIDE_B, SLIDE_C) for _, t in slide]


def _cer(a: str, b: str) -> float:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / max(1, len(b))


ENGINES = ["tesseract", "rapidocr"] if find_tesseract() else ["rapidocr"]


@pytest.fixture
def engine(request):
    """An engine whose ONNX sessions are released even when the test fails."""
    with Engine(prefer_tesseract=request.param) as eng:
        yield eng


@pytest.mark.parametrize("engine,recognizer", [(e == "tesseract", e) for e in ENGINES],
                         indirect=["engine"])
def test_folder_to_docx_and_txt(sample_folder: Path, tmp_path: Path, engine: Engine, recognizer: str):
    if recognizer == "tesseract":
        assert engine.tesseract is not None, "tesseract found but the Bulgarian model failed to load"
    logs: list[str] = []
    progress: list[tuple[int, int, str]] = []

    out_docx = tmp_path / "result.docx"
    report = run_job(Job(sample_folder, out_docx, "docx"), engine=engine,
                     on_log=logs.append, on_progress=lambda d, t, n: progress.append((d, t, n)))
    assert report.ok_count == 3 and not report.failed
    assert progress[0][1] == 3 and progress[-1][0] == 3

    doc = Document(str(out_docx))
    paras = [(p.style.name, p.text) for p in doc.paragraphs if p.text.strip()]
    headings = [t for s, t in paras if s == "Heading 2"]
    assert headings == ["IMG_1.png", "IMG_2.jpg", "IMG_10.HEIC" if HEIF_SUPPORTED else "IMG_10.jpeg"]

    body = "\n".join(t for s, t in paras if s not in ("Heading 2", "Title") and not t.startswith("3 "))
    for expected in expected_texts():
        best = min(_cer(t, expected) for _, t in paras)
        limit = 0.03 if recognizer == "tesseract" else 0.10
        assert best <= limit, f"[{recognizer}] no paragraph close to: {expected!r} (best CER {best:.1%})"

    kinds = {t: s for s, t in paras}
    assert kinds.get("неорганизирано") == "List Bullet"
    assert kinds.get("СПОРЕД ФОРМАТА НА ОРГАНИЗАЦИЯ НА ОТДИХА") == "Slide Heading"
    # the two-line bullet was re-assembled into one list item
    assert any(s == "List Bullet" and t.startswith("организирано от туристически") for s, t in paras)
    # order of images follows natural sort: IMG_1, IMG_2, IMG_10
    assert body.index("ОТДИХА") < body.index("ПРИДВИЖВАНЕ") < body.index("ЗАКЛЮЧЕНИЕ")

    out_txt = tmp_path / "result.txt"
    run_job(Job(sample_folder, out_txt, "txt", file_headings=False), engine=engine)
    text = out_txt.read_text(encoding="utf-8")
    assert "• неорганизирано" in text
    assert "IMG_1.png" not in text


def test_unreadable_file_is_reported_not_fatal(sample_folder: Path, tmp_path: Path):
    folder = tmp_path / "mixed"
    folder.mkdir()
    (folder / "a_broken.jpg").write_bytes(b"\xff\xd8 not really a jpeg")
    render_slide(SLIDE_A[:2], font_path=cyrillic_font_path()).save(folder / "b_ok.png")
    with Engine() as engine:
        report = run_job(Job(folder, tmp_path / "r.docx", "docx"), engine=engine)
    assert [p.source.name for p in report.failed] == ["a_broken.jpg"]
    assert report.ok_count == 1
    doc = Document(str(tmp_path / "r.docx"))
    assert any("could not process" in p.text for p in doc.paragraphs)


def test_empty_folder_raises(tmp_path: Path):
    with Engine() as engine, pytest.raises(FileNotFoundError):
        run_job(Job(tmp_path, tmp_path / "r.docx", "docx"), engine=engine)


# ---------------------------------------------------------------- real photos (if present)
REAL = Path(__file__).resolve().parent.parent / "images"
GT_FILE = Path(__file__).resolve().parent.parent / "bench" / "ground_truth.json"


@pytest.mark.skipif(not (REAL.is_dir() and GT_FILE.is_file() and find_tesseract()),
                    reason="sample photos, ground truth or tesseract not available")
def test_real_slide_photos_accuracy(tmp_path: Path):
    """Regression guard on the real lecture photos: every bullet slide must come out verbatim."""
    from bench.benchmark import norm_text

    gt = json.loads(GT_FILE.read_text(encoding="utf-8"))
    with Engine() as engine:
        report = run_job(Job(REAL, tmp_path / "real.docx", "docx"), engine=engine)
    exact = total = 0
    for page in report.pages:
        entry = gt.get(page.source.name)
        if entry is None or entry.get("diagram"):
            continue
        texts = {norm_text(b.text) for b in page.blocks}
        for line in entry["lines"]:
            total += 1
            exact += norm_text(line) in texts
    assert total >= 10, "the sample corpus went missing"
    assert exact / total >= 0.97, f"only {exact}/{total} slide lines reproduced verbatim"
