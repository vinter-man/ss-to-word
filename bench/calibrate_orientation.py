"""Measure how a page reads upright vs upside down, to calibrate the rotation guards.

For every sample photo and several degradations it prints, for both orientations: the page
score, and the share of glyphs that read the same either way up. Those are the numbers behind
`ROTATION_RETRY_SCORE`, `ROTATION_SCORE_MARGIN`, `ROTATION_MIN_LINES` and `SYMMETRIC_PAGE_SHARE`
in `ss_to_word/ocr/engine.py` — in particular why the composition test has to look at both
orientations at once rather than at one read.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageFilter

from ss_to_word.images import load_image
from ss_to_word.ocr import (
    Engine,
    carries_no_orientation,
    page_score,
    text_overlap,
)
from ss_to_word.ocr.engine import ROTATIONS, SYMMETRIC_GLYPHS

VARIANTS = {
    "clean": lambda im: im,
    "blur1.6": lambda im: im.filter(ImageFilter.GaussianBlur(1.6)),
    "blur2.4": lambda im: im.filter(ImageFilter.GaussianBlur(2.4)),
    "half": lambda im: im.resize((im.width // 2, im.height // 2), Image.LANCZOS),
}


def symmetric_share(lines) -> float:
    """What `carries_no_orientation` thresholds: glyphs that survive a 180° turn."""
    glyphs = [ch for line in lines if line.conf >= 0.55 for ch in line.text if not ch.isspace()]
    if not glyphs:
        return 0.0
    digits = sum(ch.isdigit() for ch in glyphs)
    symmetric = sum(ch in SYMMETRIC_GLYPHS for ch in glyphs)
    return max(digits, symmetric) / len(glyphs)


def main() -> None:
    engine = Engine()
    rows = []
    for photo in sorted((ROOT / "images").glob("*.jpg")):
        base = load_image(photo)
        for vname, fn in VARIANTS.items():
            src = fn(base)
            upright, flipped = engine.read(src), engine.read(src.transpose(ROTATIONS[180]))
            su, sf = page_score(upright), page_score(flipped)
            ov = text_overlap(upright, flipped)
            cu, cf = symmetric_share(upright), symmetric_share(flipped)
            both = carries_no_orientation(upright) and carries_no_orientation(flipped)
            rows.append((photo.name[-12:], vname, len(upright), su, len(flipped), sf, ov, cu, cf, both))
            print(f"{photo.name[-12:]} {vname:8s} upright: n={len(upright):2d} score={su:.3f} "
                  f"sym={cu:.2f}   flipped: n={len(flipped):2d} score={sf:.3f} sym={cf:.2f}   "
                  f"diff={su - sf:+.3f} overlap={ov:.2f}", flush=True)

    # NOTE: these margins describe prose. Text built from rotation-symmetric glyphs (digits,
    # О/Х/Н/И) has no such bound — measured up to +0.420 the wrong way — which is why the engine
    # excludes those pages by composition instead of by any score threshold.
    readable = [r for r in rows if r[3] >= 0.5]          # pages worth getting right
    print(f"\npages where the upright read is usable: {len(readable)}/{len(rows)}")
    print(f"  worst flipped (junk) score:        {max(r[5] for r in rows):.3f}")
    print(f"  smallest upright score:            {min(r[3] for r in readable):.3f}")
    print(f"  smallest margin upright - flipped: {min(r[3] - r[5] for r in readable):+.3f}")
    print(f"  largest  margin flipped - upright: {max(r[5] - r[3] for r in rows):+.3f}")
    print(f"  upright pages with < 3 lines:      {sum(1 for r in readable if r[2] < 3)}")
    print(f"  upright pages with < 2 lines:      {sum(1 for r in readable if r[2] < 2)}")
    singles = [r[7] for r in rows] + [r[8] for r in rows]
    pairwise = [min(r[7], r[8]) for r in rows]
    print(f"\n  symmetric-glyph share, one read at a time: max={max(singles):.2f} "
          f"median={sorted(singles)[len(singles) // 2]:.2f}   <- a single read may exceed 0.80")
    print(f"  symmetric-glyph share, both orientations:  max={max(pairwise):.2f} "
          f"median={sorted(pairwise)[len(pairwise) // 2]:.2f}   <- what the engine thresholds")
    print(f"  real pages the pairwise test would refuse to turn: "
          f"{sum(1 for r in rows if r[9])}/{len(rows)}")
    print(f"  overlap upright vs flipped: min={min(r[6] for r in rows):.2f} "
          f"median={sorted(r[6] for r in rows)[len(rows) // 2]:.2f} max={max(r[6] for r in rows):.2f}")
    print(f"  overlap when the flip is the *correct* read (upright junk): "
          f"{sorted(r[6] for r in rows if r[3] < 0.5)[-3:]}")
    for cut in (0.5, 0.7, 0.8, 0.85, 0.9):
        missed = sum(1 for r in readable if r[5] >= cut)          # junk that would skip the retry
        extra = sum(1 for r in readable if r[3] < cut)            # good pages paying an extra pass
        print(f"  retry-below {cut:.2f}: junk above it (missed) = {missed:2d}, "
              f"good pages re-read (cost) = {extra:2d}")


if __name__ == "__main__":
    main()
