"""Engine-level edge cases: degenerate image sizes, detector failures, Tesseract plumbing."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from ss_to_word.ocr import Engine
from ss_to_word.ocr.engine import DETECT_MAX_PIXELS, _fit_detector_input
from ss_to_word.ocr.tesseract import TesseractLineRecognizer, Word, find_tesseract
from ss_to_word.pipeline import Job, run_job
from tests.conftest import cyrillic_font as font


@pytest.fixture(scope="module")
def engine():
    with Engine() as eng:        # close() drops the ONNX sessions instead of leaving them to GC
        yield eng


def text_image(size: tuple[int, int], text: str, pos=(10, 6), fs=22) -> Image.Image:
    im = Image.new("RGB", size, "white")
    ImageDraw.Draw(im).text(pos, text, font=font(fs), fill="black")
    return im


# ------------------------------------------------------------------ detector input shaping
@pytest.mark.parametrize("size", [(5000, 20), (4000, 40), (20, 5000), (400, 34)])
def test_detector_input_stays_bounded(size):
    w, h = size
    out, factor = _fit_detector_input(np.zeros((h, w, 3), dtype=np.uint8))
    oh, ow = out.shape[:2]
    effective = max(1.0, 736 / min(oh, ow))     # what the detector would still add on top
    assert oh * ow * effective * effective <= DETECT_MAX_PIXELS * 1.05
    assert factor >= 1.0


def test_normal_photo_is_passed_through_untouched():
    arr = np.zeros((1280, 960, 3), dtype=np.uint8)
    out, factor = _fit_detector_input(arr)
    assert factor == 1.0 and out is arr


def test_long_thin_image_does_not_hang(engine: Engine):
    started = time.time()
    assert engine.read(Image.new("RGB", (5000, 20), "white")) == []
    assert time.time() - started < 20


def test_tiny_images_are_skipped(engine: Engine):
    assert engine.read(Image.new("RGB", (8, 8), "white")) == []
    assert engine.read(Image.new("RGB", (1, 1), "white")) == []


def test_single_line_crop_is_not_split_into_words(engine: Engine):
    lines = engine.read(text_image((400, 34), "разполагане в сезонно"))
    assert [ln.text for ln in lines] == ["разполагане в сезонно"]


def test_wide_thin_line_is_still_read(engine: Engine):
    lines = engine.read(text_image((4000, 40), "лов и риболов и събиране на билки"))
    assert any("риболов" in ln.text for ln in lines)


def test_closed_engine_says_so():
    """`close()` is public, so using the engine afterwards must fail readably."""
    engine = Engine()
    engine.close()
    with pytest.raises(RuntimeError, match="closed"):
        engine.read(Image.new("RGB", (400, 200), "white"))
    engine.close()          # closing twice is harmless


# ------------------------------------------------------------------ failure propagation
def test_detector_failure_is_reported_not_silently_empty(engine: Engine, tmp_path: Path,
                                                         monkeypatch):
    """A broken model/runtime must surface as a per-file error, never as 'no text found'."""
    text_image((600, 80), "хотели и почивни домове").save(tmp_path / "a.png")

    def boom(*_a, **_kw):
        raise RuntimeError("onnxruntime exploded")

    monkeypatch.setattr(engine._rapid, "text_det", boom)
    report = run_job(Job(tmp_path, tmp_path / "out.docx", "docx"), engine=engine, on_log=lambda _m: None)
    assert [p.source.name for p in report.failed] == ["a.png"]
    assert "onnxruntime exploded" in (report.pages[0].error or "")
    assert report.ok_count == 0


def test_pages_without_text_are_counted(engine: Engine, tmp_path: Path):
    Image.new("RGB", (400, 300), "white").save(tmp_path / "blank.png")
    text_image((600, 80), "хотели и почивни домове").save(tmp_path / "text.png")
    logs: list[str] = []
    report = run_job(Job(tmp_path, tmp_path / "out.txt", "txt"), engine=engine, on_log=logs.append)
    assert [p.source.name for p in report.empty] == ["blank.png"]
    assert not report.failed
    assert any("No text found in 1 file" in m for m in logs)


# ------------------------------------------------------------------ tesseract wrapper
def test_tsv_parser_rejects_non_tsv_output():
    with pytest.raises(RuntimeError, match="did not return TSV"):
        TesseractLineRecognizer._parse_tsv("просто текст\n")


def test_parse_tsv_extracts_words_and_confidence():
    tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
           "5\t1\t1\t1\t1\t1\t12\t14\t60\t30\t96.5\tхотели\n"
           "5\t1\t1\t1\t1\t2\t80\t14\t20\t30\t-1\t\n"
           "5\t1\t1\t1\t1\t3\t110\t14\t90\t30\t88\tдомове\n")
    words = TesseractLineRecognizer._parse_tsv(tsv)
    assert [w.text for w in words] == ["хотели", "домове"]
    assert words[0].conf == 96.5 and words[0].left == 12


@pytest.mark.skipif(find_tesseract() is None, reason="tesseract not installed")
def test_word_boxes_are_mapped_back_to_crop_coordinates():
    rec = TesseractLineRecognizer.create("bul")
    assert rec is not None
    crop = text_image((600, 40), "хотели и почивни домове", pos=(30, 6), fs=26)
    words = rec.recognize(crop)
    assert words and isinstance(words[0], Word)
    assert 10 <= words[0].left <= 80              # first word starts near x=30 in crop pixels
    assert all(0 <= w.top < crop.height for w in words)


@pytest.mark.skipif(find_tesseract() is None, reason="tesseract not installed")
def test_light_text_on_dark_background_is_read():
    im = Image.new("RGB", (600, 44), (35, 38, 45))
    ImageDraw.Draw(im).text((12, 6), "хижи и туристически бази", font=font(26), fill=(240, 240, 240))
    rec = TesseractLineRecognizer.create("bul")
    assert rec is not None
    assert "туристически" in " ".join(w.text for w in rec.recognize(im))
