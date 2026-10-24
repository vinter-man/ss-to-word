"""Launcher: `python main.py` opens the GUI; `python main.py -i folder -o out.docx` runs headless."""

from ss_to_word.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
