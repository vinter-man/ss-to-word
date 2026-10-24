"""End-to-end job: folder of images -> OCR -> .docx / .txt. Used by both the GUI and the CLI."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .export import ExportOptions, PageText, write_docx, write_txt
from .images import find_images, load_image
from .languages import DEFAULT_LANGUAGE, tag_for
from .layout import build_blocks, usable_lines
from .ocr import Engine

log = logging.getLogger(__name__)

FORMATS = {"docx": "Word (.docx)", "txt": "Text (.txt)"}
FORMAT_SUFFIXES = {f".{fmt}" for fmt in FORMATS}


class Cancelled(Exception):
    pass


def plural(count: int, one: str, many: str) -> str:
    """"1 image" / "19 images" — the log is English, so this is all the agreement it needs."""
    return f"{count} {one if count == 1 else many}"


def expand_user(raw: str) -> str:
    """Trim a path typed by hand and expand a leading '~' (the file dialogs never produce one)."""
    text = raw.strip()
    return str(Path(text).expanduser()) if text.startswith("~") else text


def with_format_suffix(path: Path, fmt: str) -> Path:
    """Make sure `path` ends with the extension of `fmt`.

    'a.docx' + txt -> 'a.txt', 'report.v2' + docx -> 'report.v2.docx' (an unknown suffix is
    part of the name, not a format), 'a' + docx -> 'a.docx'.

    Raises ValueError for paths that cannot name a file at all ('', '.', '/').
    """
    if not path.name or path.name in (".", ".."):
        raise ValueError(f"not a file name: {path}")
    suffix = path.suffix.lower()
    if suffix == f".{fmt}":
        return path
    if suffix in FORMAT_SUFFIXES:
        return path.with_suffix(f".{fmt}")
    return path.with_name(path.name + f".{fmt}")


def check_writable(path: Path) -> str | None:
    """Return a human-readable reason why `path` cannot be written, or None if it looks fine.

    Checked before the (slow) OCR run so the user does not lose the work to a permission error.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"cannot create the folder: {exc.strerror or exc}"
    if path.is_dir():
        return "there is a folder at that path."
    probe = path if path.exists() else path.with_name(path.name + ".tmp-check")
    try:
        with open(probe, "r+b" if probe.exists() else "wb"):
            pass
    except OSError as exc:
        return f"no write access: {exc.strerror or exc}"
    finally:
        if probe is not path:
            probe.unlink(missing_ok=True)
    return None


@dataclass
class Job:
    input_dir: Path
    output_path: Path
    fmt: str = "docx"                 # "docx" | "txt"
    file_headings: bool = True
    page_breaks: bool = False
    lang: str = DEFAULT_LANGUAGE.code   # a Tesseract spec: "bul", "eng", or "bul+eng"
    prefer_tesseract: bool = True

    def __post_init__(self) -> None:
        self.input_dir = Path(self.input_dir)
        self.output_path = Path(self.output_path)
        if self.fmt not in FORMATS:
            raise ValueError(f"unknown format: {self.fmt}")


@dataclass
class Report:
    output_path: Path
    engine: str
    pages: list[PageText] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok_count(self) -> int:
        return sum(1 for p in self.pages if not p.error)

    @property
    def failed(self) -> list[PageText]:
        return [p for p in self.pages if p.error]

    @property
    def empty(self) -> list[PageText]:
        """Images that were read fine but produced no text at all."""
        return [p for p in self.pages if not p.error and not p.blocks]

    @property
    def low_confidence(self) -> list[PageText]:
        return [p for p in self.pages if not p.error and p.blocks and p.mean_conf < 0.6]


ProgressFn = Callable[[int, int, str], None]   # done, total, current file name
LogFn = Callable[[str], None]


def run_job(job: Job, *, on_progress: ProgressFn | None = None, on_log: LogFn | None = None,
            cancel: threading.Event | None = None, engine: Engine | None = None) -> Report:
    """Process every image in `job.input_dir` (alphabetical order) and write the output file."""
    say = on_log or (lambda msg: log.info(msg))
    progress = on_progress or (lambda d, t, n: None)
    t0 = time.time()

    files = find_images(job.input_dir)
    if not files:
        raise FileNotFoundError(f"no images in the folder: {job.input_dir}")
    say(f"Found {plural(len(files), 'image', 'images')}")

    if engine is None:
        say("Loading the OCR models…")
        engine = Engine(lang=job.lang, prefer_tesseract=job.prefer_tesseract)
    say(f"Engine: {engine.info.describe()}")
    for note in engine.info.notes:
        if note == "tesseract-not-found":
            say("Tesseract was not found — falling back to the built-in RapidOCR recognizer "
                "(less accurate; the README explains how to install Tesseract).")
        elif note.startswith("tesseract-cannot-load-language:"):
            say(f"Tesseract could not load the language '{note.split(':', 1)[1]}' — falling back "
                "to the built-in RapidOCR recognizer (less accurate).")
        elif note.startswith("language-fallback:"):
            asked, got = note.split(":", 1)[1].split("->")
            say(f"'{asked}' is not available in full; reading as '{got}'.")

    pages: list[PageText] = []
    for i, path in enumerate(files):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        progress(i, len(files), path.name)
        pages.append(_process_one(engine, path, say))
    progress(len(files), len(files), "")

    opts = ExportOptions(title=job.input_dir.name, file_headings=job.file_headings,
                         page_breaks=job.page_breaks, language=tag_for(job.lang))
    if job.fmt == "docx":
        write_docx(pages, job.output_path, opts)
    else:
        write_txt(pages, job.output_path, opts)

    report = Report(job.output_path, engine.info.describe(), pages, time.time() - t0)
    say(f"Done: {report.ok_count}/{len(files)} files in {report.seconds:.1f} s → {job.output_path}")
    if report.empty:
        say(f"No text found in {plural(len(report.empty), 'file', 'files')}: "
            + ", ".join(p.source.name for p in report.empty[:5])
            + (" …" if len(report.empty) > 5 else ""))
    return report


def _process_one(engine: Engine, path: Path, say: LogFn) -> PageText:
    page = PageText(source=path)
    try:
        image = load_image(path)
    except Exception as exc:  # unreadable / unsupported file: report and go on
        page.error = f"could not open the image ({exc.__class__.__name__}: {exc})"
        say(f"  ✗ {path.name}: {page.error}")
        return page
    try:
        read = engine.read_page(image)
        lines = read.lines
        with_tesseract = engine.tesseract is not None
        page.blocks = build_blocks(lines, np.asarray(read.image), tesseract=with_tesseract)
        good = usable_lines(lines, tesseract=with_tesseract)
        page.rotation = read.rotation
        if good:
            page.mean_conf = sum(ln.conf for ln in good) / len(good)
    except Exception as exc:
        log.exception("OCR failed for %s", path)
        page.error = f"recognition failed ({exc.__class__.__name__}: {exc})"
        say(f"  ✗ {path.name}: {page.error}")
        return page
    if not page.blocks:
        say(f"  ! {path.name}: no text found")
        return page
    words = sum(len(b.text.split()) for b in page.blocks)
    flag = "" if page.mean_conf >= 0.6 else "  (low confidence — worth checking)"
    turned = f", turned {page.rotation}°" if page.rotation else ""
    say(f"  ✓ {path.name}: {plural(len(page.blocks), 'paragraph', 'paragraphs')}, "
        f"{plural(words, 'word', 'words')}, "
        f"{page.mean_conf:.0%} confidence{turned}{flag}")
    return page
