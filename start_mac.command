#!/bin/bash
# Double-click launcher for macOS: creates a virtualenv on first run, installs dependencies,
# starts the app. Safe to re-run: a half-finished install is never reused.
cd "$(dirname "$0")" || exit 1

STAMP=".venv/.install-ok"
PIP_LOG=".venv/pip-install.log"
say_and_wait() { echo ""; echo "$1"; echo ""; read -r -p "Press Enter to close…"; exit 1; }

# Python 3.14 is listed last on purpose: the OCR wheels (onnxruntime) are published for it only
# on Apple Silicon with macOS 14+, while 3.11-3.13 work on Intel and on macOS 13 as well.
find_python() {
  for cand in python3.13 python3.12 python3.11 python3.14 python3 \
              /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    command -v "$cand" >/dev/null 2>&1 || continue
    "$cand" - <<'PY' >/dev/null 2>&1 || continue
import sys, tkinter
assert sys.version_info >= (3, 11), sys.version
tkinter.Tk().destroy()          # Tk must really start, not just import
PY
    echo "$cand"; return 0
  done
  return 1
}

if [ ! -f "$STAMP" ]; then
  PY="$(find_python)" || say_and_wait "No Python 3.11+ with a working Tk was found.
Install Python from https://www.python.org/downloads/macos/ and run this file again."
  echo "Preparing the environment ($("$PY" -V 2>&1)). The first run takes 1-3 minutes…"
  rm -rf .venv
  "$PY" -m venv .venv || say_and_wait "Could not create the environment (.venv)."
  .venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1
  # `| tee` would hide pip's exit status behind tee's, so pipefail is what makes this check work.
  set -o pipefail
  if ! .venv/bin/python -m pip install -r requirements.txt 2>&1 | tee "$PIP_LOG"; then
    if grep -qiE "No matching distribution|Could not find a version" "$PIP_LOG"; then
      say_and_wait "There are no prebuilt OCR packages for this Python on this computer.
What helps: install Python 3.13 (https://www.python.org/downloads/macos/) and run this file again.
Requirements: macOS 13 or newer; Python 3.14 needs Apple Silicon with macOS 14+.
Details in $PIP_LOG"
    fi
    say_and_wait "Could not install the dependencies - check the internet and run this file again.
Details in $PIP_LOG"
  fi
  set +o pipefail
  .venv/bin/python -c "import rapidocr, onnxruntime, cv2, numpy, docx, PIL, pillow_heif, tkinter" >/dev/null 2>&1 \
    || say_and_wait "The environment did not install completely. Run this file again and it will
be rebuilt from scratch."
  touch "$STAMP"
fi

if ! command -v tesseract >/dev/null 2>&1 && [ ! -x /opt/homebrew/bin/tesseract ] && [ ! -x /usr/local/bin/tesseract ]; then
  echo "Hint: for the best accuracy install Tesseract:  brew install tesseract"
fi

exec .venv/bin/python main.py
