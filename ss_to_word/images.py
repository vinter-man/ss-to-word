"""Finding and loading input images (PNG, JPEG, HEIC/HEIF and friends)."""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageOps

# HEIC/HEIF (iPhone) support is provided by pillow-heif; registering once is enough.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:  # pragma: no cover - only when the optional wheel is missing
    HEIF_SUPPORTED = False

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".jpe", ".jfif",
    ".heic", ".heif", ".hif",
    ".webp", ".bmp", ".tif", ".tiff", ".gif",
}

_NUM_RE = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple:
    """Sort key: case-insensitive alphabetical, with embedded numbers compared numerically.

    'IMG_2.jpg' < 'IMG_10.jpg', 'a.png' == 'A.png' ordering-wise, and 'IMG_2.jpg' < 'IMG_2 (1).jpg'
    (the stem is compared before the extension).
    """
    path = Path(name)
    parts = _NUM_RE.split(path.stem.casefold())
    tokens = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts if p)
    return tokens, path.suffix.casefold()


def find_images(folder: Path) -> list[Path]:
    """Return image files directly inside `folder`, sorted alphabetically (natural order)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(str(folder))
    files = [
        p for p in folder.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(files, key=lambda p: natural_key(p.name))


# Modes whose values do not fit in a byte: a plain convert("RGB") clips them to black/white.
_WIDE_MODES = {"I", "I;16", "I;16B", "I;16L", "I;16N", "F"}


def load_image(path: Path) -> Image.Image:
    """Load an image as RGB with EXIF orientation applied (phone photos are often rotated in EXIF)."""
    with Image.open(path) as im:
        im.load()
        im = ImageOps.exif_transpose(im)
        if im.mode in _WIDE_MODES:
            im = _scale_to_byte(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        return im


def _scale_to_byte(im: Image.Image) -> Image.Image:
    """Map a 16/32-bit grayscale image onto 0..255 using its own min/max."""
    lo, hi = im.getextrema()
    if not isinstance(lo, (int, float)) or hi <= lo:
        return im.convert("L")
    scale = 255.0 / (hi - lo)
    return im.point(lambda v: (v - lo) * scale, mode="L")
