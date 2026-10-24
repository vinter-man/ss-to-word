"""Turn recognized lines into document blocks: headings, bullet items and paragraphs.

Everything here is geometry + light text heuristics; no OCR happens in this module, which keeps
it unit-testable with synthetic lines.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

import numpy as np

from .ocr.engine import Line

Kind = Literal["heading", "bullet", "paragraph"]


@dataclass
class Block:
    kind: Kind
    text: str


# Characters that a real line of text never starts with (when followed by a space). When the text
# detector includes the bullet/check-mark glyph in the box, Tesseract renders it as one of these.
_MARKER_CHARS = "\"„“”‟«»‹›'‘’‚`´:;|_~^*•·●○◦▪■□▫►▶➢➤✓✔√✗×+>=.,"
_LEADING_MARKER_RE = re.compile(rf"^[{re.escape(_MARKER_CHARS)}]+(\s+|(?=\S))")
_OPENING_QUOTES = "„“\"'«‟‚"
_CLOSING_QUOTES = "“”\"'»‟"
_TRAILING_JUNK_RE = re.compile(r"[\s|_~^`´]+$")

MIN_CONF_TESSERACT = 0.55   # garbage from pictures/handwriting scores < 0.5 on the benchmark set
MIN_CONF_RAPID = 0.60


# ----------------------------------------------------------------------------- text cleaning
def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("­", "")          # soft hyphen
    text = re.sub(r"\s+", " ", text).strip()
    text = _TRAILING_JUNK_RE.sub("", text)
    return text


def _alnum_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    return sum(c.isalnum() for c in chars) / len(chars)


def _strip_leading_marker(text: str, later_text: str) -> tuple[str, bool]:
    """Remove a bullet-like marker at the start of the line. Returns (text, had_marker).

    An opening quote glued to a word („дума) is kept when a closing quote follows within the
    next few lines, because then it is most likely a real quotation, not a check-mark artifact.
    """
    m = _LEADING_MARKER_RE.match(text)
    if not m:
        return text, False
    marker, rest = text[:m.end()], text[m.end():]
    if not rest:
        return text, False
    attached = not marker[-1].isspace()
    if attached:
        if marker.strip() in _OPENING_QUOTES or marker.strip()[-1] in _OPENING_QUOTES:
            if any(q in rest or q in later_text for q in _CLOSING_QUOTES):
                return text, False
        elif set(marker.strip()) <= set(".,"):   # "...дума" / ".5" fragments are not markers
            return text, False
    return rest, True


def usable_lines(lines: list[Line], *, tesseract: bool = True) -> list[Line]:
    """Lines that pass the confidence/noise filter (the ones that end up in the document)."""
    min_conf = MIN_CONF_TESSERACT if tesseract else MIN_CONF_RAPID
    return [ln for ln in lines if ln.text and not _low_quality(ln, min_conf)]


def _low_quality(line: Line, min_conf: float) -> bool:
    if line.conf < min_conf:
        return True
    words = line.words or []
    # more than half of the words are guesses: picture or handwriting noise
    return len(words) >= 2 and sum(w.conf < 30 for w in words) * 2 > len(words)


# ----------------------------------------------------------------------------- bullets
def _glyph_from_words(line: Line) -> bool:
    """Tesseract read the bullet glyph as a separate 1-char token far from the first real word."""
    words = line.words or []
    if len(words) < 2:
        return False
    first, second = words[0], words[1]
    h = max(1.0, line.text_height)
    gap = second.left - (first.left + first.width)
    if len(first.text) != 1 or first.text in "-–—":   # dashes may be real text (dialogue)
        return False
    return not first.text.isalnum() or gap > 0.45 * h


def _warp_strip(rgb: np.ndarray, line: Line, start: float, end: float) -> np.ndarray | None:
    """Cut a strip aligned with the (possibly tilted) line: from `start` to `end` line-heights
    measured from the line's left edge along its baseline direction (negative = left of it)."""
    import cv2

    p0, p1, _, p3 = (line.box[i].astype(np.float64) for i in range(4))
    h = line.text_height
    d = p1 - p0
    norm = float(np.hypot(*d))
    if norm < 1 or h < 8:
        return None
    d /= norm
    quad = np.array([p0 + d * start * h, p0 + d * end * h, p3 + d * end * h, p3 + d * start * h],
                    dtype=np.float32)
    w_out, h_out = max(4, round((end - start) * h)), max(4, round(h))
    dst = np.array([[0, 0], [w_out, 0], [w_out, h_out], [0, h_out]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(rgb, m, (w_out, h_out), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REPLICATE)


def _glyph_left_of_box(line: Line, rgb: np.ndarray) -> bool:
    """Look for one small compact blob (•, ✓, –, of any colour) immediately left of the line."""
    import cv2

    h = line.text_height
    if h < 8 or line.x0 < 0.5 * h:
        return False
    region = _warp_strip(rgb, line, -1.7, -0.08)
    text_region = _warp_strip(rgb, line, 0.0, min(3.0, line.width / h))
    if region is None or text_region is None:
        return False
    region = region.astype(np.int16)
    bg = np.median(region.reshape(-1, 3), axis=0)
    text_contrast = float(np.percentile(np.abs(text_region.astype(np.int16) - bg).max(axis=2), 90))
    thr = max(15.0, min(30.0, 0.5 * text_contrast))
    ink = (np.abs(region - bg).max(axis=2) > thr).astype(np.uint8)
    if ink.mean() > 0.35:                         # region is mostly "ink": a picture, not a glyph
        return False
    n, _, stats, centroids = cv2.connectedComponentsWithStats(ink, connectivity=8)
    blobs = 0
    for i in range(1, n):
        bx, _by, bw, bh, area = stats[i]
        if area < 6 or bw < 3 or bh < 3:
            continue                              # speckle
        if bw > 1.05 * h or bh > 0.95 * h:
            return False                          # something big on the left: other content
        if bx == 0 and bw > 0.5 * h:
            return False                          # runs in from the left edge: not a glyph
        cy = centroids[i][1] / max(1, region.shape[0])
        if 0.15 <= cy <= 0.85 and bw >= 0.08 * h and bh >= 0.08 * h:
            blobs += 1
    return 1 <= blobs <= 2


# ----------------------------------------------------------------------------- headings
def _is_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters)


# ----------------------------------------------------------------------------- geometry
@dataclass
class _Item:
    line: Line
    text: str
    kind: Kind = "paragraph"
    bullet: bool = False
    marker_in_box: bool = False   # the detector box included the bullet glyph itself
    # geometry after virtual deskew (used for ordering and grouping only)
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0

    @property
    def h(self) -> float: return self.line.text_height
    @property
    def cy(self) -> float: return (self.y0 + self.y1) / 2


def _dominant_angle(lines: list[Line]) -> float:
    """Median tilt (degrees) of the longer lines; photos of slides are rarely perfectly level."""
    angles = []
    for ln in lines:
        p0, p1 = ln.box[0], ln.box[1]
        if math.hypot(*(p1 - p0)) >= 3 * ln.text_height:
            angles.append(math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0])))
    if not angles:
        return 0.0
    angles.sort()
    return angles[len(angles) // 2]


def _assign_geometry(items: list[_Item], angle_deg: float) -> None:
    a = math.radians(-angle_deg)
    c, s = math.cos(a), math.sin(a)
    rot = np.array([[c, -s], [s, c]], dtype=np.float64)
    for it in items:
        pts = it.line.box.astype(np.float64) @ rot.T
        it.x0, it.y0 = float(pts[:, 0].min()), float(pts[:, 1].min())
        it.x1, it.y1 = float(pts[:, 0].max()), float(pts[:, 1].max())


def _same_band(a: _Item, b: _Item) -> bool:
    return abs(a.cy - b.cy) < 0.5 * min(a.h, b.h)


def _h_overlap(a: _Item, b: _Item) -> float:
    inter = min(a.x1, b.x1) - max(a.x0, b.x0)
    return inter / max(1.0, min(a.x1 - a.x0, b.x1 - b.x0))


def _order_paragraphs(paragraphs: list[list[_Item]]) -> list[list[_Item]]:
    """Top-to-bottom by first line; paragraphs starting in the same band go left-to-right."""
    pending = sorted(paragraphs, key=lambda p: p[0].cy)
    ordered: list[list[_Item]] = []
    while pending:
        anchor = pending[0][0]
        band = [p for p in pending if _same_band(anchor, p[0])]
        band.sort(key=lambda p: p[0].x0)
        ordered.extend(band)
        taken = {id(p) for p in band}
        pending = [p for p in pending if id(p) not in taken]
    return ordered


# ----------------------------------------------------------------------------- assembly
def _join(prev: str, nxt: str) -> str:
    if prev.endswith("-") and len(prev) > 1 and prev[-2].isalpha() and nxt[:1].isalpha():
        return prev[:-1] + nxt if nxt[0].islower() else prev + nxt
    return f"{prev} {nxt}"


def _continues(prev: _Item, cur: _Item) -> float | None:
    """Vertical gap if `cur` can be the next line of the block ending with `prev`, else None."""
    if cur.bullet:
        return None
    if (cur.kind == "heading") != (prev.kind == "heading"):
        return None
    h = max(1.0, min(prev.h, cur.h))
    if cur.cy <= prev.cy or _same_band(prev, cur) or _h_overlap(prev, cur) < 0.3:
        return None                                    # side by side / other column
    gap = cur.y0 - prev.y1
    if gap > 0.6 * h:                                  # visibly larger vertical gap
        return None
    if cur.kind == "heading":                          # headings: centred or left-aligned, be lenient
        return gap
    dx = cur.x0 - prev.x0
    lo, hi = (-0.4 * h, 1.4 * h) if prev.marker_in_box else (-0.6 * h, 0.6 * h)
    if lo <= dx <= hi:
        return gap                                     # left-aligned continuation
    dcx = (cur.x0 + cur.x1 - prev.x0 - prev.x1) / 2
    if abs(dcx) <= 0.3 * h and gap <= 0.35 * h:
        return gap                                     # centred text (diagram boxes, captions)
    return None


def _group_paragraphs(items: list[_Item]) -> list[list[_Item]]:
    """Attach every line (top to bottom) to the closest paragraph it can continue."""
    paragraphs: list[list[_Item]] = []
    for it in sorted(items, key=lambda x: x.cy):
        best, best_gap = None, None
        for para in paragraphs:
            gap = _continues(para[-1], it)
            if gap is not None and (best_gap is None or gap < best_gap):
                best, best_gap = para, gap
        if best is None:
            paragraphs.append([it])
        else:
            best.append(it)
    return paragraphs


def build_blocks(lines: list[Line], rgb: np.ndarray | None, *, tesseract: bool = True) -> list[Block]:
    """Clean, filter, order and group recognized lines into blocks.

    `rgb` is the full-resolution HxWx3 image (used only to spot bullet glyphs next to lines).
    """
    candidates = usable_lines(lines, tesseract=tesseract)
    if not candidates:
        return []

    items = [_Item(ln, normalize_text(ln.text)) for ln in candidates]
    _assign_geometry(items, _dominant_angle(candidates))
    items.sort(key=lambda it: it.cy)

    kept: list[_Item] = []
    for i, it in enumerate(items):
        if not it.text:
            continue
        later = " ".join(o.text for o in items[i + 1:i + 4])
        it.text, marker = _strip_leading_marker(it.text, later)
        if not marker and it.line.words and _glyph_from_words(it.line):
            it.text = normalize_text(" ".join(w.text for w in it.line.words[1:]))
            marker = True
        if not it.text or _alnum_ratio(it.text) < 0.4 or not any(c.isalnum() for c in it.text):
            continue
        it.bullet = it.marker_in_box = marker
        if not it.bullet and rgb is not None:
            it.bullet = _glyph_left_of_box(it.line, rgb)
        kept.append(it)
    if not kept:
        return []

    heights = sorted(it.h for it in kept)
    median_h = heights[len(heights) // 2]
    for it in kept:
        if it.bullet:
            it.kind = "bullet"
        elif it.h >= 1.35 * median_h or _is_caps(it.text):
            it.kind = "heading"

    blocks: list[Block] = []
    for para in _order_paragraphs(_group_paragraphs(kept)):
        text = para[0].text
        for it in para[1:]:
            text = _join(text, it.text)
        blocks.append(Block(para[0].kind, text))
    return blocks
