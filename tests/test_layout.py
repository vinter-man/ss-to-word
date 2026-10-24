"""Unit tests for layout heuristics with synthetic line geometry (no OCR involved)."""

from __future__ import annotations

import numpy as np

from ss_to_word.layout import Block, _strip_leading_marker, build_blocks, normalize_text
from ss_to_word.ocr.engine import Line
from ss_to_word.ocr.tesseract import Word


def line(text: str, x: float, y: float, h: float = 30, width: float | None = None,
         conf: float = 0.95, words: list[Word] | None = None) -> Line:
    w = width if width is not None else 14 * len(text)
    box = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float32)
    return Line(box=box, text=text, conf=conf, words=words)


def kinds_and_texts(blocks: list[Block]) -> list[tuple[str, str]]:
    return [(b.kind, b.text) for b in blocks]


# ------------------------------------------------------------------ text cleaning
def test_normalize_collapses_whitespace_and_trailing_junk():
    assert normalize_text("  хора   на \tтруд |") == "хора на труд"
    assert normalize_text("дума­та") == "думата"


def test_strip_leading_marker_cases():
    assert _strip_leading_marker('" хотели и почивни домове', "") == ("хотели и почивни домове", True)
    assert _strip_leading_marker(": мотели, ротели", "") == ("мотели, ротели", True)
    assert _strip_leading_marker("• пеша или с велосипед", "") == ("пеша или с велосипед", True)
    assert _strip_leading_marker("„индивидуални (еднофамилни)", "и обществени вили") == ("индивидуални (еднофамилни)", True)
    # a real quotation keeps its opening quote when it is closed later
    assert _strip_leading_marker("„духовната нагласа“", "") == ("„духовната нагласа“", False)
    assert _strip_leading_marker("„Това е дълъг цитат", "който продължава“ тук") == ("„Това е дълъг цитат", False)
    # dashes and ellipses are legitimate line starts
    assert _strip_leading_marker("— Здравей — каза той.", "") == ("— Здравей — каза той.", False)
    assert _strip_leading_marker("...и така нататък", "") == ("...и така нататък", False)
    assert _strip_leading_marker("2-3 часа", "") == ("2-3 часа", False)


# ------------------------------------------------------------------ grouping
def test_continuation_lines_merge_and_bullets_split():
    lines = [
        line("ЗАГЛАВИЕ НА СЛАЙДА", 200, 40, h=44),
        line("• първа точка която", 100, 120, words=[Word("•", 90, 0, 5, 12, 12), Word("първа", 95, 40, 0, 70, 30)]),
        line("продължава на втори ред", 128, 156),
        line("• втора точка", 100, 192, words=[Word("•", 90, 0, 5, 12, 12), Word("втора", 95, 40, 0, 70, 30)]),
    ]
    blocks = build_blocks(lines, None)
    assert kinds_and_texts(blocks) == [
        ("heading", "ЗАГЛАВИЕ НА СЛАЙДА"),
        ("bullet", "първа точка която продължава на втори ред"),
        ("bullet", "втора точка"),
    ]


def test_large_vertical_gap_starts_new_paragraph():
    lines = [line("първи абзац", 100, 100), line("втори абзац", 100, 180)]
    assert kinds_and_texts(build_blocks(lines, None)) == [("paragraph", "първи абзац"), ("paragraph", "втори абзац")]


def test_tight_lines_with_same_indent_merge():
    lines = [line("първи ред на абзаца", 100, 100), line("втори ред на абзаца", 102, 134)]
    assert kinds_and_texts(build_blocks(lines, None)) == [("paragraph", "първи ред на абзаца втори ред на абзаца")]


def test_hyphenated_line_break_is_joined():
    lines = [line("тази дума е пренесе-", 100, 100), line("на на следващия ред", 100, 134)]
    assert build_blocks(lines, None)[0].text == "тази дума е пренесена на следващия ред"
    lines = [line("РЕКРЕАЦИОННО-", 100, 100, h=44), line("ТУРИСТИЧЕСКИ ДЕЙНОСТИ", 60, 150, h=44)]
    assert build_blocks(lines, None)[0].text == "РЕКРЕАЦИОННО-ТУРИСТИЧЕСКИ ДЕЙНОСТИ"


def test_two_columns_read_left_then_right_per_band():
    lines = [
        line("ляво горе", 50, 100), line("дясно горе", 600, 100),
        line("ляво долу", 50, 200), line("дясно долу", 600, 200),
    ]
    assert [b.text for b in build_blocks(lines, None)] == ["ляво горе", "дясно горе", "ляво долу", "дясно долу"]


def test_column_continuation_is_not_broken_by_the_other_column():
    # right column has a two-line box; left column has a line in between vertically
    lines = [
        line("на кон или с животински", 600, 100, width=322),   # centre x = 761
        line("транзитно", 50, 125),
        line("впряг", 726, 134, width=70),                       # centred under the line above
    ]
    texts = [b.text for b in build_blocks(lines, None)]
    assert texts == ["на кон или с животински впряг", "транзитно"]


def test_centered_lines_merge_only_when_tight():
    tight = [line("с автомобил, мотоциклет", 500, 100), line("или друг вид МПС", 540, 132)]
    assert [b.text for b in build_blocks(tight, None)] == ["с автомобил, мотоциклет или друг вид МПС"]
    loose = [line("с велосипед", 540, 100), line("комбинирано", 540, 158)]
    assert [b.text for b in build_blocks(loose, None)] == ["с велосипед", "комбинирано"]


# ------------------------------------------------------------------ filtering & headings
def test_low_confidence_and_junk_lines_are_dropped():
    lines = [
        line("нормален текст", 100, 100),
        line("камовки состороули", 100, 200, conf=0.37),
        line("|| -- ..", 100, 300),
        line("Общв плать ащ", 100, 400, conf=0.9,
             words=[Word("Общв", 10, 0, 0, 10, 10), Word("плать", 12, 20, 0, 10, 10), Word("ащ", 90, 40, 0, 10, 10)]),
    ]
    assert [b.text for b in build_blocks(lines, None)] == ["нормален текст"]


def test_tall_line_becomes_heading_even_without_caps():
    lines = [line("Заглавие", 100, 40, h=60), line("обикновен ред", 100, 140), line("още един ред", 100, 200)]
    assert build_blocks(lines, None)[0].kind == "heading"


def test_glyph_token_far_from_first_word_marks_bullet():
    words = [Word("е", 60, 0, 10, 10, 10), Word("неорганизирано", 95, 40, 0, 200, 30)]
    lines = [line("е неорганизирано", 100, 100, words=words)]
    assert kinds_and_texts(build_blocks(lines, None)) == [("bullet", "неорганизирано")]


def test_bullet_glyph_detected_from_image():
    rgb = np.full((200, 800, 3), 250, dtype=np.uint8)
    rgb[108:122, 62:76] = (0, 160, 60)          # small green dot left of the line
    lines = [line("зелена точка", 100, 100), line("без точка", 100, 150)]
    assert kinds_and_texts(build_blocks(lines, rgb)) == [("bullet", "зелена точка"), ("paragraph", "без точка")]


def test_empty_input():
    assert build_blocks([], None) == []
