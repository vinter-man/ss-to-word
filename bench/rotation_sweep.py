"""Rotate every sample photo, in several qualities, and check the document survives the trip.

Each photo is fed to the program turned by 90°, 180° and 270° and the resulting document is
compared with the one the upright photo produces. Reported per quality:

* wrong turns   — the program turned the page the wrong way;
* left as-is    — it refused to turn a page that needed it;
* text lost     — of those, the ones where the document lost most of its text.

This is the regression net behind the rotation guards in `ss_to_word/ocr/engine.py`; it takes a
few minutes, which is why the assertions that guard day-to-day behaviour live in
`tests/test_rotation.py` instead.
"""

from __future__ import annotations

import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
from PIL import Image, ImageFilter

from ss_to_word.images import load_image
from ss_to_word.layout import build_blocks
from ss_to_word.ocr import Engine, PageRead
from ss_to_word.ocr.engine import ROTATIONS

KEPT_TEXT_FLOOR = 0.6          # below this share of the upright characters, call the text lost
JPEG_SCRATCH = Path(tempfile.gettempdir()) / "ss_to_word_sweep.jpg"


def recompress(image: Image.Image, quality: int = 45) -> Image.Image:
    image.save(JPEG_SCRATCH, quality=quality)
    return Image.open(JPEG_SCRATCH).convert("RGB")


QUALITIES = {
    "clean": lambda im: im,
    "blur1.6": lambda im: im.filter(ImageFilter.GaussianBlur(1.6)),
    "half": lambda im: im.resize((im.width // 2, im.height // 2), Image.LANCZOS),
    "quarter": lambda im: im.resize((im.width // 4, im.height // 4), Image.LANCZOS),
    "jpeg45": recompress,
}


def paragraphs(engine: Engine, read: PageRead) -> list[str]:
    blocks = build_blocks(read.lines, np.asarray(read.image), tesseract=engine.tesseract is not None)
    return [block.text for block in blocks]


def characters(texts: list[str]) -> Counter:
    return Counter(ch for text in texts for ch in text if not ch.isspace())


def kept(reference: Counter, produced: Counter) -> float:
    if not reference:
        return 1.0
    return sum((reference & produced).values()) / sum(reference.values())


def main() -> None:
    with Engine() as engine:
        for quality, degrade in QUALITIES.items():
            wrong = left_alone = lost = total = 0
            for photo in sorted((ROOT / "images").glob("*.jpg")):
                upright = degrade(load_image(photo))
                reference = characters(paragraphs(engine, engine.read_page(upright)))
                for degrees in (90, 180, 270):
                    read = engine.read_page(upright.transpose(ROTATIONS[degrees]))
                    produced = paragraphs(engine, read)
                    survived = kept(reference, characters(produced))
                    total += 1
                    if read.rotation == (360 - degrees) % 360:
                        continue
                    if read.rotation == 0:
                        left_alone += 1
                    else:
                        wrong += 1
                    if survived < KEPT_TEXT_FLOOR:
                        lost += 1
                        print(f"LOST {quality:8s} {photo.name[-12:]} fed={degrees:3d} "
                              f"turned={read.rotation:3d} kept={survived:.2f} {produced[:2]}",
                              flush=True)
            print(f"{quality:8s}: {total} cases, wrong turns={wrong}, left as-is={left_alone}, "
                  f"text lost={lost}", flush=True)


if __name__ == "__main__":
    main()
