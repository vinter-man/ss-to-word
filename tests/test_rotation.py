"""Images whose EXIF orientation is missing or wrong must still come out readable."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from ss_to_word.layout import build_blocks
from ss_to_word.ocr import (
    Engine,
    carries_no_orientation,
    confident_chars,
    is_label_page,
    landscape_share,
    letter_share,
    page_score,
    text_overlap,
)
from ss_to_word.ocr.engine import Line
from tests.conftest import SLIDE_A, cyrillic_font, cyrillic_font_path, render_slide

ROTATE = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
          270: Image.Transpose.ROTATE_90}


def line(x: float, y: float, w: float, h: float, conf: float = 0.9) -> Line:
    box = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float32)
    return Line(box=box, text="текст", conf=conf)


def test_landscape_share_detects_a_turned_page():
    upright = [line(0, 0, 200, 30), line(0, 40, 180, 30), line(0, 80, 220, 30)]
    turned = [line(0, 0, 30, 200), line(40, 0, 30, 180), line(80, 0, 30, 220)]
    assert landscape_share(upright) == 1.0
    assert landscape_share(turned) == 0.0
    assert landscape_share([]) == 1.0


def test_page_score_rewards_confident_text():
    assert page_score([]) == 0.0
    good = [line(0, 0, 200, 30, conf=0.95), line(0, 40, 200, 30, conf=0.95)]
    bad = [line(0, 0, 200, 30, conf=0.2), line(0, 40, 200, 30, conf=0.3)]
    assert page_score(good) > 0.9
    assert page_score(bad) == 0.0
    assert page_score(good + bad) == pytest.approx(0.475, abs=0.05)


def test_confident_chars_counts_only_confident_lines():
    lines = [line(0, 0, 200, 30, conf=0.95), line(0, 40, 200, 30, conf=0.2)]
    lines[0].text, lines[1].text = "абв", "ггг"
    counted = confident_chars(lines)
    assert counted == {"а": 1, "б": 1, "в": 1}
    assert confident_chars([]) == {}


def test_text_overlap_compares_characters_not_order():
    def page(texts, conf=0.9):
        out = []
        for i, t in enumerate(texts):
            ln = line(0, i * 40, 200, 30, conf=conf)
            ln.text = t
            out.append(ln)
        return out

    same = page(["хотели", "домове"])
    shuffled = page(["домове", "хотели"])
    assert text_overlap(same, shuffled) == 1.0                 # a turn only changes the order
    assert text_overlap(same, page(["ыыыы", "ююю"])) == 0.0
    assert text_overlap(same, page(["хотели"])) == pytest.approx(0.5)
    assert text_overlap(same, page(["хотели", "домове", "и", "почивни"])) < 1.0   # extra text counts
    assert text_overlap([], []) == 1.0                         # nothing read either way
    # one side confident, the other not: nothing in common
    assert text_overlap(page(["хотели"], conf=0.1), page(["ыыы"])) == 0.0
    assert text_overlap(page(["хотели"]), page(["хотели"], conf=0.1)) == 0.0


def test_letter_share():
    def page(texts, conf=0.9):
        out = []
        for i, t in enumerate(texts):
            ln = line(0, i * 40, 200, 30, conf=conf)
            ln.text = t
            out.append(ln)
        return out

    assert letter_share(page(["хотели и домове"])) == 1.0
    assert letter_share(page(["0 1 8 6 9 0", "1 8 0 9 6 1"])) == 0.0
    assert letter_share(page(["2019 година"])) == pytest.approx(6 / 10)
    assert letter_share([]) == 0.0
    assert letter_share(page(["хотели"], conf=0.1)) == 0.0


def test_is_label_page_and_no_orientation():
    def page(texts):
        out = []
        for i, t in enumerate(texts):
            ln = line(0, i * 40, 200, 30, conf=0.9)
            ln.text = t
            out.append(ln)
        return out

    # short labels: the layout itself says nothing about which way up the page is
    assert is_label_page(page(["1", "2", "3", "4"]))
    assert is_label_page(page(["РЕЗУЛТАТИ", "1", "2", "3", "4", "5", "6", "7", "8", "9"]))
    assert not is_label_page(page(["хотели и почивни домове", "хижи", "мотели"]))
    assert not is_label_page([])

    # glyphs that survive a 180° turn, whatever the layout
    assert carries_no_orientation(page(["0 1 8 6 9 0", "1 8 0 9 6 1", "8 0 1 6 9 8"]))
    assert carries_no_orientation(page(["2019", "2020", "2021"]))
    assert carries_no_orientation(page(["ОХОХ ННН ХОХО", "НОН ОХО ННОО", "ХХХ ООО НННН"]))
    assert not carries_no_orientation(page(["2019 година", "2020 година", "2021 година"]))
    assert not carries_no_orientation(page(["Според вида на основната база", "за настаняване"]))
    assert not carries_no_orientation([])


@pytest.fixture(scope="module")
def engine():
    with Engine() as eng:        # close() drops the ONNX sessions instead of leaving them to GC
        yield eng


def sample_photos() -> list[Path]:
    return sorted(Path(__file__).resolve().parent.parent.joinpath("images").glob("*.jpg"))


def strip_exif(image: Image.Image) -> Image.Image:
    copy = Image.new("RGB", image.size)
    copy.paste(image)
    return copy


@pytest.mark.parametrize("degrees", [0, 90, 180, 270])
def test_slide_photo_without_exif_is_turned_back(engine: Engine, degrees: int):
    """A real slide photo rotated in pixels (no EXIF hint) must produce the same document."""
    photos = sample_photos()
    if not photos:
        pytest.skip("sample photos not available")
    original = Image.open(photos[0]).convert("RGB")
    turned = original if degrees == 0 else original.transpose(ROTATE[degrees])

    read = engine.read_page(turned)
    blocks = build_blocks(read.lines, np.asarray(read.image),
                          tesseract=engine.tesseract is not None)
    assert read.rotation == (360 - degrees) % 360   # the turn that undoes the one applied above
    assert blocks and blocks[0].kind == "heading"
    # the document must match the one the upright photo produces, whichever photo that is
    upright = engine.read_page(original)
    assert texts_of(engine, read) == texts_of(engine, upright)


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("degrees", [90, 180, 270])
def test_every_sample_photo_is_turned_back_in_reading_order(engine: Engine, index: int,
                                                            degrees: int):
    """Every sample photo, every rotation: the document must come back unchanged."""
    photos = sample_photos()
    if len(photos) <= index:
        pytest.skip("sample photos not available")
    original = strip_exif(Image.open(photos[index]).convert("RGB"))
    upright = engine.read_page(original)
    turned = engine.read_page(original.transpose(ROTATE[degrees]))

    assert turned.rotation == (360 - degrees) % 360
    assert texts_of(engine, turned) == texts_of(engine, upright), "same order as the upright photo"


@pytest.mark.parametrize("name", ["slide-03.jpg"])
def test_blurred_upside_down_photo_is_turned_back(engine: Engine, name: str):
    """Regression: a blurred flipped page reads as *confident* nonsense.

    The junk read scored 0.604 — above the old 0.5 retry threshold — and the page had only two
    lines, below the old 3-line minimum, so the flip was never even tried and the document got
    the nonsense ('ес ез2ии итединоипинАф…') at 60% confidence.
    """
    from PIL import ImageFilter

    photo = Path(__file__).resolve().parent.parent / "images" / name
    if not photo.is_file():
        pytest.skip("sample photos not available")
    blurred = strip_exif(Image.open(photo).convert("RGB")).filter(ImageFilter.GaussianBlur(1.6))
    upright = engine.read_page(blurred)
    assert upright.rotation == 0, "precondition: the blurred page still reads upright"

    flipped = engine.read_page(blurred.transpose(ROTATE[180]))
    assert flipped.rotation == 180
    assert texts_of(engine, flipped) == texts_of(engine, upright)


def texts_of(engine: Engine, read) -> list[str]:
    return [b.text for b in build_blocks(read.lines, np.asarray(read.image),
                                         tesseract=engine.tesseract is not None)]


def test_upright_page_is_not_rotated_and_costs_one_pass(engine: Engine):
    """A page that reads well is never read twice (the flip check is for poor reads only)."""
    image = render_slide(SLIDE_A, font_path=cyrillic_font_path())
    calls = []
    original_read = engine.read

    def counting_read(img):
        calls.append(img.size)
        return original_read(img)

    engine.read = counting_read  # type: ignore[method-assign]
    try:
        read = engine.read_page(image)
    finally:
        del engine.read          # drop the instance attribute, not shadow it with the bound method
    assert read.rotation == 0
    assert len(calls) == 1, "a good page must not be read more than once"


def narrow_label_page(kind: str) -> Image.Image:
    """A page whose lines are taller than wide although it is upright: digits / single letters."""
    from PIL import ImageDraw
    if kind == "digits":
        im = Image.new("RGB", (900, 468), "white")
        draw = ImageDraw.Draw(im)
        draw.text((300, 30), "РЕЗУЛТАТИ", font=cyrillic_font(44), fill="black")
        cell = cyrillic_font(40)
        for row in range(3):
            for col in range(3):
                draw.text((260 + col * 160, 150 + row * 100), str(row * 3 + col + 1),
                          font=cell, fill="black")
        return im
    im = Image.new("RGB", (400, 500), "white")
    draw = ImageDraw.Draw(im)
    letter = cyrillic_font(44)
    for i, char in enumerate("АБВГД"):
        draw.text((180, 40 + i * 90), char, font=letter, fill="black")
    return im


def blurred_digit_column() -> Image.Image:
    """A photo-like column of digits: confident, narrow, and destroyed by any rotation."""
    from PIL import ImageDraw, ImageFilter
    im = Image.new("RGB", (400, 640), "white")
    draw = ImageDraw.Draw(im)
    font = cyrillic_font(44)
    for i, char in enumerate("018181"):
        draw.text((170, 40 + i * 90), char, font=font, fill="black")
    return im.filter(ImageFilter.GaussianBlur(1.4))


def test_blurred_digit_column_survives(engine: Engine):
    """Regression: Tesseract reads turned digits confidently as junk, so a score/character-count
    gate alone accepted the rotation and the digits were lost."""
    image = blurred_digit_column()
    upright = [ln.text for ln in engine.read(image)]
    assert "".join(upright) == "018181", f"precondition failed: {upright}"

    read = engine.read_page(image)
    assert read.rotation == 0
    assert [ln.text for ln in read.lines] == upright


@pytest.mark.parametrize("kind,expected", [("digits", "РЕЗУЛТАТИ"), ("letters", "А")])
def test_short_label_page_is_not_turned(engine: Engine, kind: str, expected: str):
    """Digits in a table look 'sideways' geometrically; turning them would destroy the text."""
    image = narrow_label_page(kind)
    upright = [ln.text for ln in engine.read(image)]
    assert expected in upright, "precondition: the page reads correctly when left alone"

    read = engine.read_page(image)
    assert read.rotation == 0
    assert [ln.text for ln in read.lines] == upright


def test_quarter_size_photo_produces_no_text_in_any_orientation(engine: Engine):
    """This particular photo is unreadable at quarter size, whatever the rotation guess is.

    That is the regime where the two wrong turns of the degraded sweep live: the document comes
    out empty either way, which is why no extra guard is spent on it. Other photos still read at
    quarter size, so this is a statement about this photo, not about the size in general.
    """
    photos = sample_photos()
    if not photos:
        pytest.skip("sample photos not available")
    photo = Path(__file__).resolve().parent.parent / "images" / "slide-02.jpg"
    if not photo.is_file():
        pytest.skip("sample photo not available")
    big = strip_exif(Image.open(photo).convert("RGB"))
    tiny = big.resize((big.width // 4, big.height // 4), Image.LANCZOS)
    for degrees in (0, 90, 180, 270):
        image = tiny if degrees == 0 else tiny.transpose(ROTATE[degrees])
        assert texts_of(engine, engine.read_page(image)) == []


def digit_page() -> Image.Image:
    """Rows of rotation-symmetric digits: 0, 1 and 8 survive a flip and 6 and 9 swap."""
    from PIL import ImageDraw
    image = Image.new("RGB", (1000, 390), "white")
    draw = ImageDraw.Draw(image)
    font = cyrillic_font(46)
    for i, row in enumerate(("0 1 8 6 9 0", "1 8 0 9 6 1", "8 0 1 6 9 8")):
        draw.text((60, 40 + i * 110), row, font=font, fill="black")
    return image


def test_upside_down_photo_whose_junk_reads_as_digits_is_still_turned(engine: Engine):
    """The noise of an upside-down read often comes out as digits.

    Judging "these glyphs carry no orientation" on that read alone made the program refuse to
    turn a perfectly good photo, so the test is applied to both orientations at once.
    """
    photo = Path(__file__).resolve().parent.parent / "images" / "slide-03.jpg"
    if not photo.is_file():
        pytest.skip("sample photos not available")
    upright = strip_exif(Image.open(photo).convert("RGB"))
    junk = engine.read(upright.transpose(ROTATE[180]))
    assert carries_no_orientation(junk), "precondition: the flipped read looks like digits"

    read = engine.read_page(upright.transpose(ROTATE[180]))
    assert read.rotation == 180
    assert texts_of(engine, read) == texts_of(engine, engine.read_page(upright))


def test_page_of_digits_is_never_flipped(engine: Engine):
    """Regression: a flipped page of digits can score higher than the upright one by chance.

    Accepting it dropped a row and scrambled the rest, and the run still reported 77% confidence.
    """
    image = digit_page()
    upright = [ln.text for ln in engine.read(image)]
    assert upright, "precondition: the digits are read"

    read = engine.read_page(image)
    assert read.rotation == 0
    assert [ln.text for ln in read.lines] == upright


def test_auto_rotate_can_be_switched_off(engine: Engine):
    image = render_slide(SLIDE_A, font_path=cyrillic_font_path()).transpose(Image.Transpose.ROTATE_180)
    assert engine.read_page(image, auto_rotate=False).rotation == 0
