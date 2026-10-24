"""Tests for the path helpers and error reporting that guard a long OCR run."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from ss_to_word.cli import main as cli_main
from ss_to_word.pipeline import check_writable, with_format_suffix


@pytest.mark.parametrize("bad", ["", ".", "..", "/"])
def test_with_format_suffix_rejects_non_file_paths(bad: str):
    with pytest.raises(ValueError):
        with_format_suffix(Path(bad), "docx")


def test_with_format_suffix():
    assert with_format_suffix(Path("a.docx"), "docx") == Path("a.docx")
    assert with_format_suffix(Path("a.DOCX"), "docx") == Path("a.DOCX")
    assert with_format_suffix(Path("a.docx"), "txt") == Path("a.txt")
    assert with_format_suffix(Path("a.txt"), "docx") == Path("a.docx")
    assert with_format_suffix(Path("a"), "docx") == Path("a.docx")
    # an unknown suffix is part of the name, not a format to replace
    assert with_format_suffix(Path("lecture.v2"), "docx") == Path("lecture.v2.docx")
    assert with_format_suffix(Path("photo.pdf"), "docx") == Path("photo.pdf.docx")
    assert with_format_suffix(Path("/тест/лекция 1"), "txt") == Path("/тест/лекция 1.txt")


def test_check_writable_creates_missing_parents(tmp_path: Path):
    target = tmp_path / "a" / "b" / "r.docx"
    assert check_writable(target) is None
    assert target.parent.is_dir()
    assert not target.exists()          # the probe file must be cleaned up


def test_check_writable_rejects_directory(tmp_path: Path):
    (tmp_path / "r.docx").mkdir()
    assert "folder" in (check_writable(tmp_path / "r.docx") or "")


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="read-only directories are not enforced for this user")
def test_check_writable_reports_permission_error(tmp_path: Path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert "write access" in (check_writable(locked / "r.docx") or "")
    finally:
        locked.chmod(0o700)


def test_check_writable_keeps_existing_file(tmp_path: Path):
    existing = tmp_path / "r.txt"
    existing.write_text("важные данные", encoding="utf-8")
    assert check_writable(existing) is None
    assert existing.read_text(encoding="utf-8") == "важные данные"


@pytest.fixture
def no_ocr(monkeypatch):
    """Fail loudly if a path check lets the (slow) OCR start."""
    from ss_to_word import cli

    monkeypatch.setattr(cli, "run_job", lambda *_a, **_kw: pytest.fail("run_job must not be called"))


def test_cli_rejects_a_folder_as_output(tmp_path: Path, capsys, no_ocr):
    (tmp_path / "out.docx").mkdir()
    assert cli_main(["-i", str(tmp_path), "-o", str(tmp_path / "out.docx")]) == 2
    assert "not a folder" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["", ".", "..", "/"])
def test_cli_rejects_paths_that_cannot_name_a_file(tmp_path: Path, capsys, no_ocr, bad: str):
    assert cli_main(["-i", str(tmp_path), "-o", bad]) == 2
    assert "file name" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="read-only directories are not enforced for this user")
def test_cli_rejects_unwritable_output_before_ocr(tmp_path: Path, capsys, no_ocr):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert cli_main(["-i", str(tmp_path), "-o", str(locked / "out.docx")]) == 1
        assert "cannot save" in capsys.readouterr().err
    finally:
        locked.chmod(0o700)


def test_cli_missing_input_folder(tmp_path: Path, capsys):
    code = cli_main(["-i", str(tmp_path / "nope"), "-o", str(tmp_path / "out.txt")])
    assert code == 1
    assert "error" in capsys.readouterr().err


def test_cli_requires_both_arguments(tmp_path: Path, capsys):
    assert cli_main(["-i", str(tmp_path)]) == 2
    assert "--output" in capsys.readouterr().err


@pytest.mark.parametrize("spelling,expected", [
    ("ru", "rus"), ("Українська", "ukr"), ("bul+eng", "bul+eng"), ("eng+ukr", "eng+ukr"),
])
def test_cli_understands_language_spellings(spelling: str, expected: str):
    from ss_to_word.cli import resolve_language

    assert resolve_language(spelling) == expected


def test_cli_warns_about_a_language_it_does_not_ship(capsys):
    from ss_to_word.cli import resolve_language

    assert resolve_language("klingon") == "klingon"      # passed through to Tesseract
    warning = capsys.readouterr().err
    assert "klingon" in warning and "bul" in warning
