"""Checks on the files the repository itself ships.

A Windows checkout is all-or-nothing: one path Windows cannot create and `git checkout` aborts,
so the clone is unusable rather than merely incomplete. That happened here — the sample photos
were named after the clock that took them, `09:49:07.jpg`, and no Windows machine could clone
the repository at all. Nothing on macOS notices, which is why it is asserted rather than
remembered.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file
WINDOWS_FORBIDDEN_CHARS = set('<>:"|?*\\') | {chr(c) for c in range(32)}
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def tracked_paths() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            capture_output=True, check=True, text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [p for p in out.split("\0") if p]


@pytest.mark.parametrize("path", tracked_paths())
def test_every_tracked_path_can_exist_on_windows(path: str):
    for part in path.split("/"):
        bad = sorted(WINDOWS_FORBIDDEN_CHARS & set(part))
        assert not bad, f"{path!r}: Windows cannot create a name containing {bad}"
        assert part.rstrip(" .") == part, f"{path!r}: Windows strips trailing spaces and dots"
        stem = part.split(".")[0].upper()
        assert stem not in WINDOWS_RESERVED_NAMES, f"{path!r}: {stem} is a reserved device name"


def test_tracked_paths_do_not_collide_case_insensitively():
    """Windows and macOS default to case-insensitive filesystems; Linux does not."""
    seen: dict[str, str] = {}
    for path in tracked_paths():
        other = seen.setdefault(path.lower(), path)
        assert other == path, f"{path!r} and {other!r} differ only in case"
