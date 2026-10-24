"""Command line entry point. Without arguments the GUI starts.

Everything here is English, like the log: the window is the part that speaks the user's
language, and it has its own switcher.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .languages import DEFAULT_LANGUAGE, LANGUAGES, find
from .pipeline import FORMATS, Job, check_writable, expand_user, run_job, with_format_suffix


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ss-to-word",
        description="Read the text on the photos in a folder into one Word or text document.",
    )
    p.add_argument("-i", "--input", type=Path, help="folder with the images")
    p.add_argument("-o", "--output", type=Path, help="output file (.docx or .txt)")
    p.add_argument("-f", "--format", choices=sorted(FORMATS),
                   help="output format (default: from the extension of -o)")
    p.add_argument("--no-file-headings", action="store_true",
                   help="do not put the file name before each image's text")
    p.add_argument("--page-breaks", action="store_true",
                   help="start every image on a new page (docx only)")
    spellings = ", ".join(f"{lang.code} ({lang.name})" for lang in LANGUAGES)
    p.add_argument("--lang", default=DEFAULT_LANGUAGE.code, metavar="LANG",
                   help=f"language of the text on the images: {spellings}. "
                        "Several at once are allowed, e.g. bul+eng, but every extra language "
                        f"slows recognition down. Default: {DEFAULT_LANGUAGE.code}")
    p.add_argument("--no-tesseract", action="store_true",
                   help="use the built-in RapidOCR recognizer instead of Tesseract")
    p.add_argument("--gui", action="store_true", help="open the window")
    p.add_argument("-v", "--verbose", action="store_true", help="verbose log")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def resolve_language(spelling: str) -> str:
    """Turn what the user typed into a Tesseract language spec, warning about unknown parts.

    Unknown parts are passed through rather than rejected: someone may have installed another
    Tesseract language system-wide, and that is their business — but silence would look like
    the program had understood them.
    """
    parts = []
    for part in spelling.split("+"):
        language = find(part)
        if language is None:
            known = ", ".join(lang.code for lang in LANGUAGES)
            print(f"note: '{part}' is not one of the languages this program ships ({known}); "
                  "trying it as a Tesseract language code", file=sys.stderr)
            parts.append(part)
        else:
            parts.append(language.code)
    return "+".join(parts)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.gui or (args.input is None and args.output is None):
        from .gui import main as gui_main

        gui_main()
        return 0

    if args.input is None or args.output is None:
        print("error: --input and --output are both required without the window", file=sys.stderr)
        return 2

    args.input = Path(expand_user(str(args.input)))
    args.output = Path(expand_user(str(args.output)))
    if args.output.is_dir():
        print(f"error: give a file name for the result, not a folder: {args.output}", file=sys.stderr)
        return 2
    lang = resolve_language(args.lang)
    fmt = args.format or ("txt" if args.output.suffix.lower() == ".txt" else "docx")
    try:
        output = with_format_suffix(args.output, fmt)
    except ValueError as exc:
        print(f"error: give a file name for the result ({exc})", file=sys.stderr)
        return 2
    if output != args.output:
        print(f"note: saving as {output}")
    problem = check_writable(output)
    if problem:
        print(f"error: cannot save to {output}: {problem}", file=sys.stderr)
        return 1
    job = Job(input_dir=args.input, output_path=output, fmt=fmt,
              file_headings=not args.no_file_headings, page_breaks=args.page_breaks,
              lang=lang, prefer_tesseract=not args.no_tesseract)
    try:
        run_job(job, on_log=print)
    except NotADirectoryError as exc:
        print(f"error: not a folder with images: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: could not write the file: {exc}", file=sys.stderr)
        return 1
    return 0
