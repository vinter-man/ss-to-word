from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def cyrillic_font_path() -> str:
    """A TrueType font that has Cyrillic glyphs, or skip — there is nothing to render without one."""
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    pytest.skip("no TrueType font with Cyrillic glyphs available")


def cyrillic_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(cyrillic_font_path(), size)


SLIDE_A = [
    ("heading", "СПОРЕД ФОРМАТА НА ОРГАНИЗАЦИЯ НА ОТДИХА"),
    ("bullet", "неорганизирано"),
    ("bullet", "организирано от учреждения, предприятия и училища"),
    ("bullet", "организирано от туристически и други специализирани фирми"),
]
SLIDE_B = [
    ("heading", "НАЧИН НА ПРИДВИЖВАНЕ"),
    ("bullet", "пеша или с велосипед"),
    ("bullet", "с превозно средство с животинска тяга"),
    ("text", "Ръководството обяви, че ще има допълнителни занятия през юли и през септември."),
]
SLIDE_C = [
    ("heading", "ЗАКЛЮЧЕНИЕ"),
    ("text", "Повишена култура и знания за значението на отдиха в природна среда, "
             "нови разбирания в медицината, хигиената, профилактиката и други."),
]


def render_slide(lines: list[tuple[str, str]], *, font_path: str, width: int = 1400,
                 dark: bool = False, tilt_deg: float = 0.0) -> Image.Image:
    """Render a synthetic slide. `lines` = [(kind, text)], kind in {"heading", "bullet", "text"}.

    Long lines wrap at roughly 55 characters so paragraph re-assembly gets exercised.
    """
    fg, bg = ((235, 235, 235), (40, 44, 52)) if dark else ((20, 20, 20), (250, 250, 250))
    title_font = ImageFont.truetype(font_path, 44)
    body_font = ImageFont.truetype(font_path, 32)
    img = Image.new("RGB", (width, 100), bg)
    draw = ImageDraw.Draw(img)
    y, rows = 60, []
    for kind, text in lines:
        f = title_font if kind == "heading" else body_font
        wrapped = _wrap(text, 48 if kind == "heading" else 55)
        for i, part in enumerate(wrapped):
            rows.append((kind, part, i == 0, f, y))
            y += int(f.size * 1.35)
        y += int(f.size * 0.6)
    img = Image.new("RGB", (width, y + 60), bg)
    draw = ImageDraw.Draw(img)
    for kind, part, first, f, yy in rows:
        x = 120 if kind == "heading" else 160
        if kind == "bullet" and first:
            draw.ellipse((x - 46, yy + 14, x - 30, yy + 30), fill=fg)
        draw.text((x, yy), part, font=f, fill=fg)
    if tilt_deg:
        img = img.rotate(tilt_deg, resample=Image.BICUBIC, expand=True, fillcolor=bg)
    return img


def _wrap(text: str, limit: int) -> list[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > limit:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out
