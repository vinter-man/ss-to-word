"""OCR engine.

Pipeline per image:
  1. RapidOCR (PP-OCR DB detector) finds text lines as rotated quads. It is robust to photos:
     perspective, uneven lighting, pictures next to text.
  2. Each line is cropped/deskewed from the full-resolution image and recognized by
     Tesseract with the Bulgarian model (0.4% CER on the benchmark set, see bench/).
     If Tesseract is not installed, RapidOCR's own cyrillic recognizer is used (2.6% CER).
"""

from __future__ import annotations

import copy
import gc
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from .tesseract import MODELS_DIR, TesseractLineRecognizer, find_tesseract, tesseract_version

CYRILLIC_REC_MODEL = MODELS_DIR / "cyrillic_PP-OCRv5_rec_mobile.onnx"
DETECT_MAX_SIDE = 2000    # detector input is capped at this size; crops come from the full image
DETECT_MIN_SIDE = 736     # the detector upscales a smaller side to this (its own `limit_side_len`)
DETECT_MAX_PIXELS = 8_000_000   # cap on the detector's input tensor (a 5000x20 strip would be 135 MP)
DETECT_MAX_UPSCALE = 4.0  # beyond this the detector starts splitting one line into single words
MIN_IMAGE_SIDE = 12       # anything thinner cannot hold a line of text
# Calibrated on the sample photos and blurred/downscaled copies of them (bench/calibrate_orientation.py):
# an upside-down read never scored above 0.604, a usable upright read never below 0.538, and the
# gap between the two orientations was at least +0.362 the right way and at most +0.101 the wrong
# way. Those last two numbers hold for prose only: text built from rotation-symmetric glyphs has
# no such bound (measured up to +0.420 the wrong way), so no score margin can separate those —
# they are excluded by `carries_no_orientation` before any turn is considered.
ROTATION_RETRY_SCORE = 0.8   # a page reading worse than this is also read upside down
ROTATION_MIN_LINES = 2       # one line carries no page layout worth re-reading
ROTATION_MIN_TEXT_KEPT = 0.85  # a turn that keeps this share of the text is the same page
ROTATION_SCORE_MARGIN = 0.3    # …or the turned copy must read this much better than the original
ROTATION_MIN_LETTERS = 0.6     # a page is only flipped when the flipped copy reads as words
LABEL_PAGE_SHARE = 0.8        # …or that many of its lines are labels rather than sentences
LABEL_MAX_CHARS = 2           # "1.", "А", "42" — too short to imply a reading direction
SYMMETRIC_PAGE_SHARE = 0.8    # …or that many of its glyphs read the same either way up
# Glyphs that survive a 180° turn as themselves or as each other. A single read of a real slide
# reaches 0.95 of these when it is the upside-down one (noise decodes as digits), which is why
# one read may not veto a turn; both orientations of a real page together never exceed 0.38, and
# a page built only from these glyphs measures 1.00 (bench/calibrate_orientation.py).
SYMMETRIC_GLYPHS = frozenset("01689OoIlSsZzNnHXxОоХхНнИиЖжФф")
ROTATIONS = {90: Image.Transpose.ROTATE_270,    # degrees clockwise -> PIL operation
             180: Image.Transpose.ROTATE_180,
             270: Image.Transpose.ROTATE_90}


@dataclass(eq=False)
class Line:
    """A detected text line: quad box (4x2, full-image px) and its recognized text."""
    box: np.ndarray
    text: str = ""
    conf: float = 0.0            # 0..1
    words: list | None = None    # per-word boxes in crop coordinates (Tesseract only)

    @property
    def x0(self) -> float: return float(self.box[:, 0].min())
    @property
    def y0(self) -> float: return float(self.box[:, 1].min())
    @property
    def x1(self) -> float: return float(self.box[:, 0].max())
    @property
    def y1(self) -> float: return float(self.box[:, 1].max())
    @property
    def height(self) -> float: return self.y1 - self.y0
    @property
    def width(self) -> float: return self.x1 - self.x0

    @property
    def text_height(self) -> float:
        """Height of the (possibly rotated) quad itself, not of its axis-aligned bbox."""
        p0, p3 = self.box[0], self.box[3]
        h = float(np.hypot(*(p3 - p0)))
        return h if h > 1 else self.height


def landscape_share(lines: list[Line], min_conf: float = 0.55) -> float:
    """Share of confident lines that are wider than tall — ~1.0 upright, ~0.0 on a turned page."""
    good = [ln for ln in lines if ln.conf >= min_conf] or lines
    if not good:
        return 1.0
    return sum(ln.width > ln.height for ln in good) / len(good)


@dataclass
class PageRead:
    """Lines of one image, together with the image its boxes belong to."""
    lines: list[Line]
    image: Image.Image
    rotation: int = 0          # degrees clockwise applied to the original image


def confident_chars(lines: list[Line], min_conf: float = 0.55) -> Counter:
    """Characters that came from lines the recognizer was confident about, as a multiset."""
    return Counter(ch for ln in lines if ln.conf >= min_conf for ch in ln.text if not ch.isspace())


def letter_share(lines: list[Line], min_conf: float = 0.55) -> float:
    """Share of the confidently read characters that are letters rather than digits or signs."""
    glyphs = [ch for ln in lines if ln.conf >= min_conf for ch in ln.text if not ch.isspace()]
    if not glyphs:
        return 0.0
    return sum(ch.isalpha() for ch in glyphs) / len(glyphs)


def is_label_page(lines: list[Line], min_conf: float = 0.55) -> bool:
    """Is this a page of very short labels — a column of single letters, a table of digits?

    Its "lines" are single glyphs, so neither the geometry nor the recognizer's confidence says
    anything about which way up the page is, and turning it can only scramble the text. Reading
    it top to bottom is as good as left to right, so it is left exactly as it came in.
    """
    texts = [ln.text.strip() for ln in lines if ln.conf >= min_conf and ln.text.strip()]
    if not texts:
        return False
    return sum(len(t) <= LABEL_MAX_CHARS for t in texts) >= LABEL_PAGE_SHARE * len(texts)


def carries_no_orientation(lines: list[Line], min_conf: float = 0.55) -> bool:
    """Is this text made of glyphs that look the same either way up?

    Digits are: flipped digits are still digits (0, 1 and 8 stay themselves, 6 and 9 swap), so a
    turned copy of a table of numbers reads just as confidently while its rows come out
    scrambled. So are О, Х, Н, И, Ж, Ф and their lookalikes — a page built from those can even
    score *better* upside down, by chance.

    Judged on one read alone this is not enough to refuse a turn: the *upside-down* read of an
    ordinary slide is noise, and noise often comes out as digits. It only means something when
    both orientations read this way, which is what `read_page` requires.
    """
    glyphs = [ch for ln in lines if ln.conf >= min_conf for ch in ln.text if not ch.isspace()]
    if not glyphs:
        return False
    digits = sum(ch.isdigit() for ch in glyphs)
    symmetric = sum(ch in SYMMETRIC_GLYPHS for ch in glyphs)
    return max(digits, symmetric) >= SYMMETRIC_PAGE_SHARE * len(glyphs)


def text_overlap(before: list[Line], after: list[Line], min_conf: float = 0.55) -> float:
    """How much of the confidently read text the two reads have in common (0..1).

    Turning a page does not change which glyphs are on it, only their order, so a correct
    rotation keeps nearly all of them while a wrong one loses most. Measured on the sample
    photos (pages that already read well): correct turn 0.86-1.00, wrong turn 0.00-0.40.
    Unlike a confidence score this cannot be fooled by Tesseract reading turned glyphs
    confidently as different characters.
    """
    a, b = confident_chars(before, min_conf), confident_chars(after, min_conf)
    total = max(sum(a.values()), sum(b.values()))
    if not total:
        return 1.0
    return sum((a & b).values()) / total


def page_score(lines: list[Line], min_conf: float = 0.55) -> float:
    """0..1 quality of a page read: share of characters coming from confident lines.

    An upright photo scores ~0.9 and the same photo upside down ~0.0-0.2, which is how
    `read_page` spots images whose EXIF orientation is missing or wrong. It does NOT separate
    upright from sideways: each line crop is straightened on its own, so a photo turned by 90°
    reads just as confidently — that case is caught by `landscape_share` instead.
    """
    if not lines:
        return 0.0
    chars = sum(len(ln.text) for ln in lines)
    good_chars = sum(len(ln.text) for ln in lines if ln.conf >= min_conf)
    if not good_chars:
        return 0.0
    conf = sum(ln.conf * len(ln.text) for ln in lines if ln.conf >= min_conf) / good_chars
    return good_chars / max(1, chars) * conf


@dataclass
class EngineInfo:
    detector: str = ""
    recognizer: str = ""
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return f"{self.detector} + {self.recognizer}"


class Engine:
    """Loads models once; `read(image)` returns recognized lines for a PIL RGB image."""

    def __init__(self, lang: str = "bul", prefer_tesseract: bool = True, workers: int | None = None):
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import LangRec, ModelType, OCRVersion

        params = {
            "Rec.lang_type": LangRec.CYRILLIC,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,
            "Global.use_cls": False,
            "Global.log_level": "warning",
        }
        if CYRILLIC_REC_MODEL.is_file():
            params["Rec.model_path"] = str(CYRILLIC_REC_MODEL)
        self._rapid = RapidOCR(params=params)
        detector = self._rapid.cfg.Det
        self.info = EngineInfo(detector=f"RapidOCR {detector.ocr_version.value} det")
        self.tesseract: TesseractLineRecognizer | None = None
        if prefer_tesseract:
            self.tesseract = TesseractLineRecognizer.create(lang)
        if self.tesseract is not None:
            self.info.recognizer = f"Tesseract {tesseract_version(self.tesseract.exe)} ({self.tesseract.lang})"
            if self.tesseract.lang != lang:
                self.info.notes.append(f"language-fallback:{lang}->{self.tesseract.lang}")
        else:
            self.info.recognizer = "RapidOCR PP-OCRv5 cyrillic"
            if prefer_tesseract:
                self.info.notes.append("tesseract-not-found" if find_tesseract() is None
                                       else f"tesseract-cannot-load-language:{lang}")
        self.workers = workers or max(1, min(4, (os.cpu_count() or 2)))

    def close(self) -> None:
        """Release the ONNX sessions instead of leaving them to interpreter shutdown.

        Dropping them from a destructor racing with the interpreter has been seen to abort the
        process ("recursive_mutex lock failed") after everything else has finished. The engine
        cannot be used afterwards; build a new one.
        """
        self._rapid = None
        gc.collect()

    @property
    def _ocr(self):
        """The RapidOCR models, with a readable error once the engine has been closed."""
        if self._rapid is None:
            raise RuntimeError("this Engine has been closed; create a new one")
        return self._rapid

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ detection
    def detect(self, image: Image.Image) -> list[Line]:
        """Detect text lines. Boxes are returned in full-image pixel coordinates."""
        if min(image.size) < MIN_IMAGE_SIDE:
            return []
        scale = min(1.0, DETECT_MAX_SIDE / max(image.size))
        det_img = image if scale == 1.0 else image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)
        bgr = np.ascontiguousarray(np.asarray(det_img)[:, :, ::-1])
        bgr, extra = _fit_detector_input(bgr)
        res = self._ocr.text_det(bgr)
        if res.boxes is None or len(res.boxes) == 0:
            return []
        boxes = np.asarray(res.boxes, dtype=np.float32) / (scale * extra)
        lines = [Line(box=b) for b in boxes]
        return _drop_duplicates(lines)

    # ---------------------------------------------------------------- recognition
    def read_page(self, image: Image.Image, *, auto_rotate: bool = True) -> PageRead:
        """Read one image, re-trying rotated copies when the result looks upside down.

        Photos normally carry their orientation in EXIF (applied on load), but screenshots of
        rotated photos and images stripped of EXIF do not.
        """
        lines = self.read(image)
        best = PageRead(lines, image, 0)
        if not auto_rotate or len(lines) < ROTATION_MIN_LINES:
            return best
        if is_label_page(lines):
            return best
        original_score = page_score(lines)
        sideways = landscape_share(lines) < 0.5   # lines run down the page: it is turned 90°
        if not sideways and original_score >= ROTATION_RETRY_SCORE:
            return best                # reads well and its lines run across the page: it is upright
        if sideways:
            return self._turn_sideways(image, lines)
        # Upside down: the page reads poorly now, so the flipped copy has to read clearly better
        # *and* come out as words. "Clearly" matters because a blurred page reads as confident
        # nonsense either way; "as words" matters because digits are symmetric — 0, 1 and 8 look
        # the same upside down and 6 and 9 swap — so a page of numbers can score better flipped
        # by pure chance, and flipping it would only scramble the rows.
        rotated = image.transpose(ROTATIONS[180])
        candidate = self.read(rotated)
        if (page_score(candidate) >= original_score + ROTATION_SCORE_MARGIN
                and letter_share(candidate) >= ROTATION_MIN_LETTERS
                and not self._same_either_way(lines, candidate)):
            return PageRead(candidate, rotated, 180)
        return best

    @staticmethod
    def _same_either_way(before: list[Line], after: list[Line]) -> bool:
        """Do both orientations read as glyphs that carry no orientation? Then do not turn."""
        return carries_no_orientation(before) and carries_no_orientation(after)

    def _turn_sideways(self, image: Image.Image, lines: list[Line]) -> PageRead:
        """Turn a page whose lines run down the page, keeping the original if text would be lost.

        Line crops are straightened one by one, so a page photographed sideways may already
        *read* fine (only its reading order is wrong) or come out as garbage, depending on which
        way it was turned. Both cases are handled here.

        A turned copy is taken as the same page when it keeps the text (`text_overlap`) or when
        it reads *much* better than the original, which means the original was garbage however
        confident it looked. Of the copies that qualify, the one that reads best wins — without
        that, a page turned the wrong way (a 180° flip of the right one) can also keep enough
        characters to pass the overlap gate and win by default.

        A page of short labels never gets here at all — `read_page` rejects those outright. Text
        made of glyphs that read the same either way up (digits, О/Х/Н/И) cannot be recognised
        from one read, so it is checked per candidate inside the loop instead.
        """
        best = PageRead(lines, image, 0)
        original_score = page_score(lines)
        best_score = -1.0
        for degrees in (90, 270):
            rotated = image.transpose(ROTATIONS[degrees])
            candidate = self.read(rotated)
            score = page_score(candidate)
            same_page = text_overlap(lines, candidate) >= ROTATION_MIN_TEXT_KEPT
            much_better = score >= original_score + ROTATION_SCORE_MARGIN
            if self._same_either_way(lines, candidate):
                continue          # symmetric glyphs both ways: the page has no readable "up"
            if (same_page or much_better) and score > best_score:
                best_score, best = score, PageRead(candidate, rotated, degrees)
        return best

    def read(self, image: Image.Image) -> list[Line]:
        """Recognized lines with boxes in the coordinates of `image` (no rotation applied)."""
        lines = self.detect(image)
        if not lines:
            return []
        bgr = np.ascontiguousarray(np.asarray(image)[:, :, ::-1])
        crops = [_crop_line(bgr, ln.box) for ln in lines]
        if self.tesseract is not None:
            self._recognize_tesseract(lines, crops)
        else:
            self._recognize_rapid(lines, crops)
        return [ln for ln in lines if ln.text]

    def _recognize_tesseract(self, lines: list[Line], crops: list[np.ndarray]) -> None:
        rec = self.tesseract
        assert rec is not None

        def work(crop: np.ndarray):
            pil = Image.fromarray(crop[:, :, ::-1])
            return rec.recognize(pil)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            results = list(pool.map(work, crops))
        for ln, words in zip(lines, results, strict=True):
            if not words:
                continue
            ln.text = " ".join(w.text for w in words)
            ln.conf = sum(w.conf for w in words) / len(words) / 100.0
            ln.words = words

    def _recognize_rapid(self, lines: list[Line], crops: list[np.ndarray]) -> None:
        from rapidocr.ch_ppocr_rec import TextRecInput

        res = self._ocr.text_rec(TextRecInput(img=crops))
        txts = list(res.txts or [])
        scores = list(res.scores or [])
        for ln, txt, sc in zip(lines, txts, scores, strict=False):
            ln.text = (txt or "").strip()
            ln.conf = float(sc)


def _fit_detector_input(bgr: np.ndarray) -> tuple[np.ndarray, float]:
    """Prepare a small image for the detector. Returns (image, upscale factor applied).

    The detector enlarges the shorter side to 736 px, which is what makes small screenshots
    readable — but on a 5000x20 strip that means a 184000x736 tensor, and on a 400x34 crop the
    21x blow-up makes it split a line into separate words. So for images below 736 px we do the
    enlargement ourselves with a sane cap and pad the short side to 736, which leaves the
    detector's own resize a no-op.
    """
    h, w = bgr.shape[:2]
    short, long = min(h, w), max(h, w)
    if short >= DETECT_MIN_SIDE:
        return bgr, 1.0
    natural = DETECT_MIN_SIDE / short
    if h * w * natural * natural <= DETECT_MAX_PIXELS and natural <= DETECT_MAX_UPSCALE:
        return bgr, 1.0                       # the detector's own upscale is harmless here
    factor = max(1.0, min(DETECT_MAX_UPSCALE, natural, DETECT_MAX_PIXELS / (DETECT_MIN_SIDE * long)))
    if factor > 1.0:
        import cv2

        bgr = cv2.resize(bgr, (max(1, round(w * factor)), max(1, round(h * factor))),
                         interpolation=cv2.INTER_CUBIC)
        h, w = bgr.shape[:2]
    pad_h, pad_w = max(0, DETECT_MIN_SIDE - h), max(0, DETECT_MIN_SIDE - w)
    if pad_h or pad_w:
        bgr = np.pad(bgr, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")
    return bgr, factor


def _crop_line(bgr: np.ndarray, box: np.ndarray) -> np.ndarray:
    from rapidocr.utils.process_img import get_rotate_crop_image

    return get_rotate_crop_image(bgr, copy.deepcopy(np.asarray(box, dtype=np.float32)))


def _drop_duplicates(lines: list[Line], iou_threshold: float = 0.5) -> list[Line]:
    kept: list[Line] = []
    for ln in lines:
        if all(_iou(ln, other) < iou_threshold for other in kept):
            kept.append(ln)
    return kept


def _iou(a: Line, b: Line) -> float:
    iw = max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))
    ih = max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0))
    inter = iw * ih
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union > 0 else 0.0
