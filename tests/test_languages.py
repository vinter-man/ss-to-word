"""The language picker: spellings, bundled models, and recognition in each language."""

from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn
from PIL import Image, ImageDraw

from ss_to_word.languages import DEFAULT_LANGUAGE, LANGUAGES, find, resolve, tag_for
from ss_to_word.ocr import Engine
from ss_to_word.ocr.tesseract import BUNDLED_TESSDATA, available_languages, find_tesseract
from ss_to_word.pipeline import Job, run_job
from tests.conftest import cyrillic_font as font

SAMPLES = {
    "bul": ["хотели и почивни домове", "разполагане в сезонно функциониращи места"],
    "rus": ["размещение в сезонно работающих местах", "гостиницы и дома отдыха"],
    "ukr": ["розміщення в сезонних місцях", "готелі та будинки відпочинку"],
    "eng": ["hotels and holiday homes", "seasonal accommodation sites"],
}


def render(lines: list[str]) -> Image.Image:
    image = Image.new("RGB", (1100, 90 + 70 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    face = font(38)
    for i, text in enumerate(lines):
        draw.text((50, 40 + i * 70), text, font=face, fill=(15, 15, 15))
    return image


# ------------------------------------------------------------------ the table itself
def test_every_language_has_a_bundled_model():
    for language in LANGUAGES:
        assert (BUNDLED_TESSDATA / f"{language.code}.traineddata").is_file(), language.code


@pytest.mark.skipif(find_tesseract() is None, reason="tesseract not installed")
def test_tesseract_can_load_every_bundled_model():
    available = available_languages(find_tesseract(), BUNDLED_TESSDATA)
    assert {language.code for language in LANGUAGES} <= available


def test_no_spelling_means_two_languages():
    seen: dict[str, str] = {}
    for language in LANGUAGES:
        assert len(set(language.spellings)) == len(language.spellings), language.code
        for spelling in language.spellings:
            other = seen.setdefault(spelling.casefold(), language.code)
            assert other == language.code, f"{spelling!r} means both {other} and {language.code}"
            assert find(spelling) is language


@pytest.mark.parametrize("spelling,code", [
    ("ru", "rus"), ("RU", "rus"), (" русский ", "rus"), ("Русский", "rus"),
    ("uk", "ukr"), ("ua", "ukr"), ("Українська", "ukr"), ("украинский", "ukr"),
    ("bg", "bul"), ("Български", "bul"), ("bulgarian", "bul"),
    ("en", "eng"), ("English", "eng"), ("английский", "eng"),
])
def test_find_accepts_the_spellings_a_user_would_type(spelling: str, code: str):
    language = find(spelling)
    assert language is not None and language.code == code


def test_unknown_language_is_reported_with_the_known_ones():
    assert find("klingon") is None
    with pytest.raises(ValueError, match="bul"):
        resolve("klingon")


def test_document_tag_follows_the_language():
    assert tag_for("ukr") == "uk-UA"
    assert tag_for("eng") == "en-US"
    assert tag_for("bul+eng") == "bg-BG"          # the first language wins
    assert tag_for("klingon") == DEFAULT_LANGUAGE.tag


# ------------------------------------------------------------------ actually reading them
@pytest.mark.skipif(find_tesseract() is None, reason="tesseract not installed")
@pytest.mark.parametrize("code", sorted(SAMPLES))
def test_each_language_is_recognized(code: str):
    with Engine(lang=code) as engine:
        assert engine.tesseract is not None and engine.tesseract.lang == code
        read = [line.text.casefold() for line in engine.read(render(SAMPLES[code]))]
    assert read == [line.casefold() for line in SAMPLES[code]]


@pytest.mark.skipif(find_tesseract() is None, reason="tesseract not installed")
def test_ukrainian_letters_missing_from_russian_are_read():
    """і, ї, є and the apostrophe are what a Russian model would get wrong.

    ґ is left out on purpose: it is rare enough that the Ukrainian model itself reads it as г
    at this size, so asserting it would test Tesseract's training data, not this program.
    """
    image = render(["їжа, під'їзд, з'їзд", "будинок і єдність"])
    with Engine(lang="ukr") as engine:
        text = " ".join(line.text for line in engine.read(image))
    for letter in "їіє'":
        assert letter in text, f"{letter!r} missing from {text!r}"


@pytest.mark.skipif(find_tesseract() is None, reason="tesseract not installed")
def test_chosen_language_reaches_the_document(tmp_path: Path):
    folder = tmp_path / "photos"
    folder.mkdir()
    render(SAMPLES["ukr"]).save(folder / "IMG_1.png")
    out = tmp_path / "result.docx"

    with Engine(lang="ukr") as engine:
        report = run_job(Job(folder, out, "docx", lang="ukr"), engine=engine, on_log=lambda _m: None)

    assert report.ok_count == 1
    doc = Document(str(out))
    assert any(SAMPLES["ukr"][0] in p.text for p in doc.paragraphs)
    assert doc.styles["Normal"].element.rPr.find(qn("w:lang")).get(qn("w:val")) == "uk-UA"
