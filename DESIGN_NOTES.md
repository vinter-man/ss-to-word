# Design notes

Why the program is shaped the way it is, and what was measured to decide. The numbers come from
the scripts in `bench/`, run over a development corpus of 19 photographed slides from one
lecture. Three of those photos ship in `images/`, with their hand transcription in
`bench/ground_truth.json`; the other sixteen are somebody else's lecture and stayed private,
so the figures below are the record of what was measured rather than something this repository
can reproduce on its own. `bench/structure.py` re-runs on the three that are here.

## Choosing the engines

Three OCR engines were run on the sample photos before any pipeline existed:

| Engine | Word recall | Word precision | Time |
|---|---|---|---|
| Tesseract on the whole photo | 78.8 % | 78.2 % | 0.3 s/photo |
| EasyOCR | 78.1 % | 76.8 % | 2.6 s/photo |
| RapidOCR (PP-OCRv5 cyrillic) | 80.7 % | 94.9 % | 0.2 s/photo |
| RapidOCR detection + Tesseract per line | **99.6 %** | **96.0 %** | 0.2 s/photo |
| RapidOCR detection + RapidOCR recognition | 95.6 % | 94.1 % | 0.2 s/photo |

The last row is the fallback the program uses when no Tesseract binary is found. Times are
wall-clock per photo on an M-series laptop, recognition only, and are there to show that the
accuracy was not bought with time — the chosen pair is as fast as the fastest single engine.

Tesseract's page segmentation is the part that fails on photographed slides: it returns empty
pages, or reads two columns as one. Its *line* recognizer with a language model is the most
accurate of the three. RapidOCR's detector is the opposite: excellent at finding lines on a
tilted, glary photo, weaker at reading them. Splitting the job along that seam is the whole
design.

Details that mattered and are easy to get wrong:

- `tessdata_fast` beat `tessdata_best` on this corpus (2.1 % vs 0.8 % CER on the line benchmark),
  and is a quarter of the size.
- Line crops are scaled to 80 px tall before recognition; 40 px and 120 px both read worse.
- Light text on a dark background is inverted first — Tesseract is trained on dark-on-light.
- Tesseract is called with `stdin`/`stdout` and `-c tessedit_create_tsv=1` rather than through
  temporary files, which keeps non-ASCII user names on Windows from breaking it. The `tsv`
  *config file* cannot be used, because config files are looked up inside the tessdata directory
  and the bundled one contains only language models.

## Orientation: the part that took the longest

Photos usually carry their orientation in EXIF, which is applied on load. Screenshots of rotated
photos and files stripped of EXIF do not, so the program has to decide by itself. Three signals
are used, and the order matters.

**Geometry** tells sideways from upright. `landscape_share` — the share of confident lines wider
than tall — is 1.00 on an upright page and 0.00 on one turned 90°. Clean separation, no
threshold worth arguing about.

**Recognizer confidence** tells upright from upside down, but only for prose, and only when the
page reads well. Measured over 76 page/degradation pairs: an upside-down read never scored above
0.604, a usable upright read never below 0.538, and the gap between the two orientations was at
least +0.362 the right way and at most +0.101 the wrong way. Hence `ROTATION_RETRY_SCORE = 0.8`
(below this a flip is tried) and `ROTATION_SCORE_MARGIN = 0.3` (the flip has to win by this much).

**Glyph composition** is what confidence cannot give. Digits survive a turn as digits — 0, 1 and
8 stay themselves and 6 and 9 swap — and so do О, Х, Н, И, Ж, Ф. On such a page the wrong flip
can score *better* than the right one: measured up to +0.420 the wrong way, above the +0.362
that a correct flip is guaranteed. No score threshold can separate those classes, so pages built
from such glyphs are excluded from rotation entirely.

Two mistakes were made here and are worth remembering:

1. A *score margin* was tried first as the fix for symmetric-glyph pages. It cannot work — the
   two classes overlap, as the numbers above show. Composition is the only signal that separates
   them.
2. The composition test was first applied to a single read. That destroyed the text of a clean
   sample photo: the *upside-down* read of an ordinary Cyrillic slide is noise, and noise often
   decodes as digits, so the page vetoed its own rescue. `bench/calibrate_orientation.py` prints
   the two shares side by side: a single read reaches 0.95 — past the 0.80 threshold — while
   both orientations of the same page together never exceed 0.38, and not one real page would
   be refused a turn. The test is therefore pairwise: a turn is refused only when *both*
   orientations look composition-ambiguous.

What the guards cost and what they buy, over 19 photos × 3 rotations per degradation:

| Degradation | Wrong turns | Left as they were | Text lost |
|---|---|---|---|
| clean | 0 | 0 | 0 |
| JPEG q45 | 0 | 1 | 1 |
| half size | 0 | 2 | 0 |
| blur 1.6 | 0 | 6 | 1 |
| quarter size | 5 | 36 | 27 |

At quarter size (about 10 px per line) the photos produce no usable text in any orientation —
all five wrong turns there yield an empty document, which is why no extra guard is spent on that
regime. A minimum-line-height guard was tried and reverted: it prevented 2 harmless wrong turns
at the price of 23 correct ones.

The second pass costs about +1 s on the 19-photo corpus (+18 %), and only 2 of the 19 photos are
read twice.

## Structure

`layout.py` turns recognized lines into headings, bullets and paragraphs using geometry, and is
deliberately free of OCR so it can be unit-tested with synthetic lines.

- Lines are virtually deskewed by the median tilt before anything is compared, so a photo taken
  at an angle groups the same way a scan does.
- Bullets are found two ways: the marker character the recognizer put at the start of the line,
  and a small compact blob to the left of the line box — any colour, since slides use green
  ticks as often as black dots.
- A page of short labels (a column of single letters) is never merged into paragraphs; the
  layout says nothing about reading order there.
- Noise from photographs and handwriting is dropped by confidence, with a stricter rule for
  lines where more than half the words are guesses.

## Things that are eyeballed, not measured

Honest boundary of the above: `_glyph_left_of_box` in `layout.py`, which decides whether a small
blob to the left of a line is a bullet, has about eight geometric constants (how far left to
look, how big a blob may be, how much ink makes a picture rather than a glyph). They were tuned
by eye on the sample photos and are covered by tests only as behaviour, not as calibration. If a
new corpus ever makes bullets go missing, that function is where to start, and it deserves the
same treatment the rotation thresholds got.

## Languages, of which there are two kinds

`languages.py` lists the languages the OCR can read — a Tesseract model code, the name in the
language itself, a BCP-47 tag for the Word proofing language, and the spellings a person might
type on the command line. Adding one is a tuple entry plus a `.traineddata` file, and a test
asserts the two stay in sync.

`i18n.py` is the separate matter of what language the *window* speaks. English by default, with
Bulgarian, Ukrainian and Russian behind the globe in the bottom-right corner. Deliberately a
flat dict of strings rather than gettext: four languages and forty keys do not need a toolchain,
and a missing key falls back to English rather than crashing. Everything else — the log, the
command line, the code, this file — is English, because it is read by whoever fixes the program
rather than by whoever uses it.

## Known gaps

- Flow charts: the text is recovered, the order of the boxes is approximate.
- A table of numbers photographed upside down is not recovered, and that costs the values rather
  than the row order (`1980` upside down reads as `0861`). There is no signal in the pixels to
  decide with; rotating before processing is the answer.
- An intermittent `recursive_mutex lock failed` abort was seen once at interpreter shutdown,
  before `Engine.close()` existed. It has not recurred in 18 runs since. If it comes back, look
  at the order in which the ONNX sessions are released.

## Calibration scripts

`bench/calibrate_orientation.py` prints, for every photo and degradation, the page score of both
orientations, the share of glyphs that read the same either way up, and a summary table of what
each candidate threshold would cost. Every number quoted above comes from its output.
`bench/rotation_sweep.py` rotates the whole corpus and compares the resulting documents, which
is how a regression in any of this shows up. Neither is part of the test suite: they take
minutes, and the assertions that guard the behaviour live in `tests/test_rotation.py`.
