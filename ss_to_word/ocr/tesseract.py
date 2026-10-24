"""Thin Tesseract wrapper: binary discovery and single-line recognition over stdin/stdout.

We deliberately do not use pytesseract: it goes through temporary files, which breaks on
Windows when the user name contains non-ASCII characters. Piping the image through stdin
avoids temp files entirely.
"""

from __future__ import annotations

import io
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
BUNDLED_TESSDATA = MODELS_DIR / "tessdata"


@dataclass
class Word:
    text: str
    conf: float  # 0..100
    left: int    # px, in the coordinates of the image that was passed in
    top: int
    width: int
    height: int


def _candidate_paths() -> list[Path]:
    env = os.environ.get("TESSERACT_CMD")
    found: list[Path] = []
    if env:
        found.append(Path(env))
    which = shutil.which("tesseract")
    if which:
        found.append(Path(which))
    system = platform.system()
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        # Windows spells these mixed-case; os.environ is case-insensitive there anyway.
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),  # noqa: SIM112
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),  # noqa: SIM112
                     os.path.join(local, "Programs"), local):
            if base:
                found.append(Path(base) / "Tesseract-OCR" / "tesseract.exe")
    elif system == "Darwin":
        found += [Path("/opt/homebrew/bin/tesseract"), Path("/usr/local/bin/tesseract"),
                  Path("/opt/local/bin/tesseract")]
    else:
        found += [Path("/usr/bin/tesseract"), Path("/usr/local/bin/tesseract")]
    return found


def _popen_kwargs() -> dict:
    kw: dict = {}
    if sys.platform == "win32":  # do not flash a console window for every call
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kw


def find_tesseract() -> Path | None:
    """Return the first working tesseract binary, or None."""
    seen = set()
    for cand in _candidate_paths():
        if cand in seen or not cand.is_file():
            continue
        seen.add(cand)
        try:
            out = subprocess.run([str(cand), "--version"], capture_output=True, timeout=20,
                                 **_popen_kwargs())
        except (OSError, subprocess.SubprocessError):
            continue
        text = (out.stdout + out.stderr).decode("utf-8", "replace")
        if out.returncode == 0 and "tesseract" in text.lower():
            return cand
    return None


def tesseract_version(exe: Path) -> str:
    out = subprocess.run([str(exe), "--version"], capture_output=True, timeout=20, **_popen_kwargs())
    text = (out.stdout or out.stderr).decode("utf-8", "replace").strip()
    lines = text.splitlines()
    return lines[0].replace("tesseract", "").strip() if lines else "?"


def available_languages(exe: Path, tessdata: Path | None) -> set[str]:
    """Languages Tesseract can actually load from `tessdata` (None = its default location)."""
    args = [str(exe), "--list-langs"]
    if tessdata is not None:
        args += ["--tessdata-dir", str(tessdata)]
    try:
        out = subprocess.run(args, capture_output=True, timeout=30, **_popen_kwargs())
    except (OSError, subprocess.SubprocessError):
        return set()
    text = (out.stdout + out.stderr).decode("utf-8", "replace")
    langs = set()
    for line in text.splitlines():
        line = line.strip()
        if line and " " not in line and not line.endswith(":"):
            langs.add(line)
    return langs


def _tessdata_candidates() -> list[Path | None]:
    """Bundled tessdata first, then whatever the system installation provides (None = default)."""
    cands: list[Path | None] = []
    if (BUNDLED_TESSDATA / "bul.traineddata").is_file():
        p = BUNDLED_TESSDATA
        if sys.platform == "win32" and not str(p).isascii():
            # Tesseract on Windows cannot open non-ASCII paths; the 8.3 short name is ASCII.
            short = _short_path(p)
            if short:
                cands.append(short)
        cands.append(p)
    prefix = os.environ.get("TESSDATA_PREFIX")
    if prefix and (Path(prefix) / "bul.traineddata").is_file():
        cands.append(Path(prefix))
    cands.append(None)
    return cands


def _short_path(p: Path) -> Path | None:  # pragma: no cover - Windows only
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(512)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(p), buf, 512)  # type: ignore[attr-defined]
        return Path(buf.value) if 0 < n < 512 else None
    except Exception:
        return None


def _median_level(im: Image.Image) -> int:
    hist = im.histogram()
    total, acc = im.width * im.height, 0
    for value, count in enumerate(hist):
        acc += count
        if acc * 2 >= total:
            return value
    return 255


class TesseractLineRecognizer:
    """Recognizes single text lines with Tesseract (`--psm 7`) using the Bulgarian model."""

    TARGET_HEIGHT = 80   # px; best CER on the benchmark set (see bench/)
    PADDING = 12

    def __init__(self, exe: Path, lang: str = "bul", tessdata: Path | None = None):
        self.exe = exe
        self.lang = lang
        self.tessdata = tessdata

    @classmethod
    def create(cls, lang: str = "bul") -> TesseractLineRecognizer | None:
        """Find tesseract and a tessdata dir that can really load every language in `lang`.

        Falls back to the languages that are available (e.g. "bul" out of "bul+eng") rather than
        letting Tesseract warn on stderr for every line and silently ignore the missing one.
        """
        exe = find_tesseract()
        if exe is None:
            return None
        wanted = [part for part in lang.split("+") if part]
        best: TesseractLineRecognizer | None = None
        for tessdata in _tessdata_candidates():
            have = available_languages(exe, tessdata)
            usable = [part for part in wanted if part in have] or wanted
            rec = cls(exe, "+".join(usable), tessdata)
            if not rec.self_test():
                continue
            if len(usable) == len(wanted):
                return rec
            best = best or rec        # keep the first partial match, look for a complete one
        return best

    def self_test(self) -> bool:
        try:
            img = Image.new("L", (160, 40), 255)
            self.recognize(img)
            return True
        except (RuntimeError, OSError, subprocess.SubprocessError):
            return False

    def _args(self) -> list[str]:
        args = [str(self.exe), "stdin", "stdout", "-l", self.lang, "--psm", "7", "--oem", "1"]
        if self.tessdata is not None:
            args += ["--tessdata-dir", str(self.tessdata)]
        # Ask for TSV explicitly instead of the `tsv` config file: config files are looked up
        # inside the tessdata dir, and ours only ships the language model.
        args += ["-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"]
        return args

    def recognize(self, line_img: Image.Image) -> list[Word]:
        """OCR one line image. Returns the recognized words with boxes in `line_img` coordinates."""
        img, scale = self._prepare(line_img)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        proc = subprocess.run(self._args(), input=buf.getvalue(), capture_output=True, timeout=120,
                              **_popen_kwargs())
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(f"tesseract failed ({proc.returncode}): {err[-500:]}")
        words = self._parse_tsv(proc.stdout.decode("utf-8", "replace"))
        for w in words:  # map back from the padded/scaled image to the original crop
            w.left = round((w.left - self.PADDING) / scale)
            w.top = round((w.top - self.PADDING) / scale)
            w.width = round(w.width / scale)
            w.height = round(w.height / scale)
        return words

    def _prepare(self, im: Image.Image) -> tuple[Image.Image, float]:
        if im.mode != "L":
            im = im.convert("L")
        scale = self.TARGET_HEIGHT / max(1, im.height)
        im = im.resize((max(1, round(im.width * scale)), self.TARGET_HEIGHT), Image.LANCZOS)
        median = _median_level(im)
        if median < 128:  # light text on a dark background: Tesseract prefers dark-on-light
            im = ImageOps.invert(im)
            median = 255 - median
        # pad with the background colour (median) so glyphs never touch the border
        canvas = Image.new("L", (im.width + 2 * self.PADDING, im.height + 2 * self.PADDING), median)
        canvas.paste(im, (self.PADDING, self.PADDING))
        return canvas, scale

    @staticmethod
    def _parse_tsv(tsv: str) -> list[Word]:
        rows = tsv.splitlines()
        if not rows or not rows[0].startswith("level\tpage_num"):
            raise RuntimeError("tesseract did not return TSV output: " + tsv[:200].strip())
        words: list[Word] = []
        for row in rows[1:]:
            cols = row.split("\t")
            if len(cols) < 12 or cols[0] != "5":  # level 5 = word
                continue
            text = cols[11].strip()
            try:
                conf = float(cols[10])
            except ValueError:
                continue
            if not text or conf < 0:
                continue
            words.append(Word(text, conf, int(cols[6]), int(cols[7]), int(cols[8]), int(cols[9])))
        return words
