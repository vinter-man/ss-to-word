"""GUI tests: the window is driven programmatically, no human and no real dialogs."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ss_to_word.pipeline import Report
from tests.conftest import SLIDE_A, cyrillic_font_path, render_slide

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="module")
def settings_file(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("gui") / "settings.json"


@pytest.fixture(scope="module")
def window(settings_file):
    """One Tk root for the whole module: creating several roots deadlocks Tk on macOS."""
    from ss_to_word import gui

    patch = pytest.MonkeyPatch()
    try:
        patch.setattr(gui, "SETTINGS_FILE", settings_file)
        # A real modal dialog would block the test run forever, so none may reach Tk. Tk swallows
        # exceptions raised inside `after` callbacks, so unexpected calls are recorded here and
        # asserted after each test (see the `app` fixture) instead of raising.
        unexpected: list[str] = []
        for name in ("showinfo", "showwarning", "showerror"):
            patch.setattr(gui.messagebox, name,
                          lambda _t, m, _n=name: unexpected.append(f"{_n}: {m}"))
        patch.setattr(gui.messagebox, "askyesno",
                      lambda *_a, **_k: unexpected.append("askyesno") or False)
        # The file dialogs are modal too; a test that wants one stubs it with its own value.
        patch.setattr(gui.filedialog, "askdirectory",
                      lambda **_k: unexpected.append("askdirectory") or "")
        patch.setattr(gui.filedialog, "asksaveasfilename",
                      lambda **_k: unexpected.append("asksaveasfilename") or "")
        try:
            root = gui.App()
        except tk.TclError as exc:                  # headless CI without a display
            pytest.skip(f"no display available: {exc}")
        root.withdraw()
        root.unexpected_dialogs = unexpected      # type: ignore[attr-defined]
        try:
            yield root
        finally:
            root.destroy()
    finally:
        patch.undo()


@pytest.fixture
def app(window):
    """The shared window, reset to a clean state before every test.

    After the test, any dialog the test did not stub itself is reported as a failure.
    """
    window._cancel.clear()
    window._worker = None
    window._report = None
    while not window._events.empty():
        window._events.get_nowait()
    window.input_var.set("")
    window.output_var.set("")
    window.log.configure(state="normal")
    window.log.delete("1.0", "end")
    window.log.configure(state="disabled")
    window.progress["value"] = 0
    window.start_btn.configure(state="normal")
    window.open_btn.configure(state="disabled")
    window.unexpected_dialogs.clear()
    yield window
    assert not window.unexpected_dialogs, f"unstubbed dialogs: {window.unexpected_dialogs}"


def pump(window, seconds: float = 0.3) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        window.update()
        time.sleep(0.02)


def wait_for_worker(window, timeout: float = 300) -> None:
    deadline = time.time() + timeout
    while window._worker is not None and window._worker.is_alive() and time.time() < deadline:
        window.update()
        time.sleep(0.05)
    pump(window, 0.6)                                # let the poll loop drain the queue


@pytest.fixture
def photos(tmp_path) -> Path:
    folder = tmp_path / "photos"
    folder.mkdir()
    render_slide(SLIDE_A, font_path=cyrillic_font_path()).save(folder / "IMG_1.png")
    return folder


def test_empty_folder_field_is_rejected(app, monkeypatch, tmp_path):
    from ss_to_word import gui

    warnings: list[str] = []
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda _t, m: warnings.append(m))
    monkeypatch.setattr(gui, "run_job", lambda *a, **k: pytest.fail("job must not start"))
    app.input_var.set("   ")
    app.output_var.set(str(tmp_path / "out.docx"))
    app._start()
    assert app._worker is None
    assert warnings and "folder" in warnings[0]


def test_missing_output_is_rejected(app, monkeypatch, photos):
    from ss_to_word import gui

    warnings: list[str] = []
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda _t, m: warnings.append(m))
    app.input_var.set(str(photos))
    app.output_var.set("")
    app._start()
    assert app._worker is None
    assert warnings and "save" in warnings[0]


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="read-only directories are not enforced for this user")
def test_unwritable_output_is_rejected_before_the_run(app, monkeypatch, photos, tmp_path):
    """Refused before the (slow) OCR starts, and without an 'overwrite?' prompt."""
    from ss_to_word import gui

    errors: list[str] = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda _t, m: errors.append(m))
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    app.input_var.set(str(photos))
    app.output_var.set(str(locked / "out.docx"))
    try:
        app._start()
    finally:
        locked.chmod(0o700)
    assert app._worker is None
    assert errors and "write access" in errors[0]


def test_format_change_rewrites_the_extension(app, tmp_path):
    from ss_to_word import gui

    app.output_var.set(str(tmp_path / "лекция.docx"))
    app.format_var.set(gui.FORMATS["txt"])
    app._sync_extension()
    assert app.output_var.get().endswith("лекция.txt")


def test_tilde_in_the_output_field_is_expanded(app, monkeypatch, photos):
    """'~/лекция.docx' must not create a folder literally named '~'."""
    from ss_to_word import gui

    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *_a, **_k: False)
    seen: list = []
    monkeypatch.setattr(gui, "run_job", lambda job, **_k: seen.append(job) or Report(
        job.output_path, "test", [], 0.0))
    app.input_var.set(str(photos))
    app.output_var.set("~/ss_to_word_test_out.docx")
    app._start()
    wait_for_worker(app)
    assert seen and str(seen[0].output_path).startswith(str(Path.home()))
    assert "~" not in str(seen[0].output_path)
    seen[0].output_path.unlink(missing_ok=True)


def test_full_run_updates_progress_log_and_settings(app, monkeypatch, photos, tmp_path,
                                                   settings_file):
    from ss_to_word import gui

    out = tmp_path / "result.docx"
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *_a, **_k: False)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda _t, m: pytest.fail(f"error: {m}"))
    app.input_var.set(str(photos))
    app.output_var.set(str(out))
    app.format_var.set(gui.FORMATS["docx"])
    app._start()
    assert app._worker is not None
    wait_for_worker(app)

    assert out.is_file() and out.stat().st_size > 5000
    assert app._report is not None and app._report.ok_count == 1
    assert app.start_btn.instate(["!disabled"]) and app.open_btn.instate(["!disabled"])
    log = app.log.get("1.0", "end")
    assert "IMG_1.png" in log and "Done" in log
    assert app.progress["value"] == app.progress["maximum"] == 1
    import json
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["input_dir"] == str(photos) and saved["format"] == "docx"


def test_overwrite_prompt_can_abort(app, monkeypatch, photos, tmp_path):
    from ss_to_word import gui

    existing = tmp_path / "result.docx"
    existing.write_bytes(b"old")
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *_a, **_k: False)
    app.input_var.set(str(photos))
    app.output_var.set(str(existing))
    app._start()
    assert app._worker is None
    assert existing.read_bytes() == b"old"


OPEN_WINDOW_SCRIPT = """
import json, sys
from pathlib import Path
sys.path.insert(0, %r)
from ss_to_word import gui
gui.SETTINGS_FILE = Path(sys.argv[1])
app = gui.App()                      # the invariant: a damaged settings file must not stop this
assert app.format_var.get() in gui.FORMATS.values(), app.format_var.get()
assert isinstance(app.headings_var.get(), (bool, int))
assert isinstance(app.input_var.get(), str)
assert isinstance(app.output_var.get(), str)
app.destroy()
print("opened")
"""


@pytest.mark.parametrize("content", [
    "[1, 2, 3]", '"строка"', "5", "null", "{oops",
    '{"file_headings": "да", "page_breaks": [1], "input_dir": 7, "format": ["docx"]}',
    '{"format": 42, "output_path": {"x": 1}}',
])
def test_corrupt_settings_file_does_not_break_startup(tmp_path, content):
    """A damaged ~/.ss_to_word.json must never stop the window from opening.

    Runs in a subprocess: Tk on macOS deadlocks when a second root is created in a process
    that already has one (the module-scoped window fixture).
    """
    broken = tmp_path / "broken.json"
    broken.write_text(content, encoding="utf-8")
    script = OPEN_WINDOW_SCRIPT % str(Path(__file__).resolve().parent.parent)
    done = subprocess.run([sys.executable, "-c", script, str(broken)],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, f"the window did not open:\n{done.stderr[-2000:]}"
    assert "opened" in done.stdout


def test_folder_as_output_is_rejected(app, monkeypatch, photos, tmp_path):
    from ss_to_word import gui

    warnings: list[str] = []
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda _t, m: warnings.append(m))
    app.input_var.set(str(photos))
    app.output_var.set(str(tmp_path))
    app._start()
    assert app._worker is None
    assert warnings and "not a folder" in warnings[0]


def test_picking_a_folder_offers_a_matching_output_name(app, monkeypatch, tmp_path):
    from ss_to_word import gui

    folder = tmp_path / "Лекция 1"
    folder.mkdir()
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **_k: str(folder))
    app.input_var.set("")
    app.output_var.set("")
    app._pick_input()
    assert app.input_var.get() == str(folder)
    assert app.output_var.get() == str(tmp_path / "Лекция 1.docx")


def test_language_choice_reaches_the_job_and_the_settings(app, monkeypatch, photos,
                                                          tmp_path, settings_file):
    import json

    from ss_to_word import gui

    jobs: list = []
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *_a, **_k: False)
    monkeypatch.setattr(gui, "run_job", lambda job, **_k: jobs.append(job) or Report(
        job.output_path, "test", [], 0.0))
    app.input_var.set(str(photos))
    app.output_var.set(str(tmp_path / "r.docx"))
    app.language_var.set("Українська")
    app._start()
    wait_for_worker(app)

    assert jobs and jobs[0].lang == "ukr"
    assert json.loads(settings_file.read_text(encoding="utf-8"))["language"] == "ukr"


def test_ocr_language_picker_offers_every_language(app):
    from ss_to_word import gui
    from ss_to_word.languages import LANGUAGES

    assert set(gui.OCR_LANGUAGE_NAMES) == {language.name for language in LANGUAGES}


def test_interface_starts_in_english_and_switches(app, settings_file):
    import json

    from ss_to_word.i18n import UI_LANGUAGES

    assert app.ui_language == "en"
    assert app.start_btn.cget("text") == "▶  Start"
    assert "Folder" in app.input_label.cget("text")

    app.set_ui_language("uk")
    assert app.start_btn.cget("text") == "▶  Старт"
    assert "Папка" in app.input_label.cget("text")
    assert "Фото" in app.title()
    assert json.loads(settings_file.read_text(encoding="utf-8"))["ui_language"] == "uk"

    labels = [app.language_menu.entrycget(i, "label") for i in range(len(UI_LANGUAGES))]
    assert labels == [language.name for language in UI_LANGUAGES]
    app.set_ui_language("en")


def test_switching_the_interface_keeps_the_work(app, photos, tmp_path):
    """The paths, the format and the OCR language are settings, not labels."""
    app.input_var.set(str(photos))
    app.output_var.set(str(tmp_path / "r.docx"))
    app.language_var.set("Українська")
    app.set_ui_language("bg")
    assert app.input_var.get() == str(photos)
    assert app._language().code == "ukr"
    assert app._fmt() == "docx"
    app.set_ui_language("en")


def test_poll_loop_survives_a_broken_event(app):
    app._events.put(("progress", "not-an-int", 3, "x"))   # malformed event
    app._events.put(("log", "после ошибки"))
    pump(app, 0.5)
    assert "после ошибки" in app.log.get("1.0", "end"), "the poll loop stopped after an error"


def test_worker_error_is_shown(app, monkeypatch, photos, tmp_path):
    from ss_to_word import gui

    errors: list[str] = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda _t, m: errors.append(m))
    monkeypatch.setattr(gui, "run_job", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("модель не загрузилась")))
    app.input_var.set(str(photos))
    app.output_var.set(str(tmp_path / "r.docx"))
    app._start()
    wait_for_worker(app)
    assert errors and "модель не загрузилась" in errors[0]
    assert app.start_btn.instate(["!disabled"])
    assert "ERROR" in app.log.get("1.0", "end")
