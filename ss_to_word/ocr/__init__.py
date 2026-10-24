"""OCR engine: RapidOCR text detection + Tesseract (or RapidOCR) line recognition."""

from .engine import (  # noqa: F401
    Engine,
    EngineInfo,
    Line,
    PageRead,
    carries_no_orientation,
    confident_chars,
    is_label_page,
    landscape_share,
    letter_share,
    page_score,
    text_overlap,
)
