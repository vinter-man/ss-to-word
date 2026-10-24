"""Tkinter window: pick the folder with photos, the output file, the format and the language.

The window is the only part of the program that speaks anything but English; the globe in its
bottom-right corner switches between the four interface languages, and the choice is remembered.
"""

from __future__ import annotations

import contextlib
import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .i18n import DEFAULT_UI_LANGUAGE, UI_LANGUAGES, find_ui_language, translate
from .languages import DEFAULT_LANGUAGE, LANGUAGES, find
from .pipeline import (
    FORMATS,
    Cancelled,
    Job,
    Report,
    check_writable,
    expand_user,
    run_job,
    with_format_suffix,
)

SETTINGS_FILE = Path.home() / ".ss_to_word.json"
LABEL_TO_FORMAT = {name: fmt for fmt, name in FORMATS.items()}
OCR_LANGUAGE_NAMES = [language.name for language in LANGUAGES]
NAME_TO_OCR_LANGUAGE = {language.name: language for language in LANGUAGES}

GLOBE_SIZE = 22
GLOBE_INK = "#5a6472"
GLOBE_HOVER = "#1f3a5f"


def _load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_settings(data: dict) -> None:
    with contextlib.suppress(OSError):       # remembering the last paths is a convenience
        SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _as_bool(value, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _as_text(value) -> str:
    return value if isinstance(value, str) else ""


def open_path(path: Path, ui_language: str = DEFAULT_UI_LANGUAGE) -> None:
    """Open a file with the default application (Word, TextEdit, Notepad…)."""
    try:
        if sys.platform == "win32":
            import os

            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        messagebox.showerror(translate("window_title", ui_language),
                             translate("cannot_open", ui_language, error=exc))


class Globe(tk.Canvas):
    """A flat globe drawn with two arcs — the interface language switcher."""

    def __init__(self, parent: tk.Misc, command) -> None:
        super().__init__(parent, width=GLOBE_SIZE, height=GLOBE_SIZE, highlightthickness=0,
                         bd=0, cursor="hand2", takefocus=True)
        self._draw(GLOBE_INK)
        self.bind("<Button-1>", command)
        self.bind("<Return>", command)
        self.bind("<Enter>", lambda _e: self._draw(GLOBE_HOVER))
        self.bind("<Leave>", lambda _e: self._draw(GLOBE_INK))

    def _draw(self, colour: str) -> None:
        self.delete("all")
        pad, size = 2, GLOBE_SIZE
        far, middle = size - pad, size / 2
        self.create_oval(pad, pad, far, far, outline=colour, width=1.4)
        self.create_line(pad, middle, far, middle, fill=colour, width=1.2)        # equator
        for width in (size * 0.26, size * 0.52):                                  # meridians
            self.create_oval(middle - width / 2, pad, middle + width / 2, far,
                             outline=colour, width=1.1)


class App(tk.Tk):
    POLL_MS = 100

    def __init__(self) -> None:
        super().__init__()
        settings = _load_settings()
        self.ui_language = self._stored_ui_language(settings)

        self.minsize(660, 500)
        self.resizable(True, True)

        self.input_var = tk.StringVar(value=_as_text(settings.get("input_dir")))
        self.output_var = tk.StringVar(value=_as_text(settings.get("output_path")))
        stored_format = _as_text(settings.get("format"))
        self.format_var = tk.StringVar(value=FORMATS.get(stored_format, FORMATS["docx"]))
        stored_language = find(_as_text(settings.get("language"))) or DEFAULT_LANGUAGE
        self.language_var = tk.StringVar(value=stored_language.name)
        self.headings_var = tk.BooleanVar(value=_as_bool(settings.get("file_headings"), True))
        self.page_breaks_var = tk.BooleanVar(value=_as_bool(settings.get("page_breaks"), False))
        self.status_var = tk.StringVar()

        self._events: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._report: Report | None = None

        self._build()
        self._retranslate()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(self.POLL_MS, self._poll)

    @staticmethod
    def _stored_ui_language(settings: dict) -> str:
        language = find_ui_language(_as_text(settings.get("ui_language")))
        return language.code if language else DEFAULT_UI_LANGUAGE

    def t(self, key: str, **fields: object) -> str:
        return translate(key, self.ui_language, **fields)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        self.input_label = ttk.Label(frame)
        self.input_label.grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", **pad)
        self.input_button = ttk.Button(frame, command=self._pick_input)
        self.input_button.grid(row=0, column=2, **pad)

        self.output_label = ttk.Label(frame)
        self.output_label.grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", **pad)
        self.output_button = ttk.Button(frame, command=self._pick_output)
        self.output_button.grid(row=1, column=2, **pad)

        self.format_label = ttk.Label(frame)
        self.format_label.grid(row=2, column=0, sticky="w", **pad)
        formats = ttk.Combobox(frame, textvariable=self.format_var, state="readonly",
                               values=list(FORMATS.values()), width=28)
        formats.grid(row=2, column=1, sticky="w", **pad)
        formats.bind("<<ComboboxSelected>>", lambda _e: self._sync_extension())

        self.ocr_language_label = ttk.Label(frame)
        self.ocr_language_label.grid(row=3, column=0, sticky="w", **pad)
        ttk.Combobox(frame, textvariable=self.language_var, state="readonly",
                     values=OCR_LANGUAGE_NAMES, width=28).grid(row=3, column=1, sticky="w", **pad)

        options = ttk.Frame(frame)
        options.grid(row=4, column=0, columnspan=3, sticky="w", **pad)
        self.headings_check = ttk.Checkbutton(options, variable=self.headings_var)
        self.headings_check.pack(side="left", padx=(0, 16))
        self.page_breaks_check = ttk.Checkbutton(options, variable=self.page_breaks_var)
        self.page_breaks_check.pack(side="left")

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=3, sticky="ew", **pad)
        self.start_btn = ttk.Button(buttons, command=self._start)
        self.start_btn.pack(side="left")
        self.cancel_btn = ttk.Button(buttons, command=self._cancel_job, state="disabled")
        self.cancel_btn.pack(side="left", padx=8)
        self.open_btn = ttk.Button(buttons, command=self._open_result, state="disabled")
        self.open_btn.pack(side="right")

        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.grid(row=6, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(frame, textvariable=self.status_var).grid(row=7, column=0, columnspan=3,
                                                            sticky="w", **pad)

        log_frame = ttk.Frame(frame)
        log_frame.grid(row=8, column=0, columnspan=3, sticky="nsew", **pad)
        frame.rowconfigure(8, weight=1)
        self.log = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        footer = ttk.Frame(frame)
        footer.grid(row=9, column=0, columnspan=3, sticky="ew", padx=8)
        self.globe = Globe(footer, self._show_language_menu)
        self.globe.pack(side="right")
        self.language_menu = tk.Menu(self, tearoff=0)
        for language in UI_LANGUAGES:
            self.language_menu.add_command(
                label=language.name,
                command=lambda code=language.code: self.set_ui_language(code),
            )

    # ------------------------------------------------------------------ interface language
    def _show_language_menu(self, event: tk.Event) -> None:
        widget = event.widget
        self.language_menu.tk_popup(widget.winfo_rootx(),
                                    widget.winfo_rooty() + widget.winfo_height())

    def set_ui_language(self, code: str) -> None:
        if code == self.ui_language:
            return
        self.ui_language = code
        self._retranslate()
        self._persist()

    def _retranslate(self) -> None:
        """Re-label everything the user can read; called at start-up and on every switch."""
        self.title(f"{self.t('window_title')} v{__version__}")
        self.input_label.configure(text=self.t("input_label"))
        self.output_label.configure(text=self.t("output_label"))
        self.format_label.configure(text=self.t("format_label"))
        self.ocr_language_label.configure(text=self.t("ocr_language_label"))
        self.input_button.configure(text=self.t("browse"))
        self.output_button.configure(text=self.t("browse"))
        self.headings_check.configure(text=self.t("file_headings"))
        self.page_breaks_check.configure(text=self.t("page_breaks"))
        self.start_btn.configure(text=self.t("start"))
        self.cancel_btn.configure(text=self.t("cancel"))
        self.open_btn.configure(text=self.t("open_result"))
        if self._worker is None or not self._worker.is_alive():
            self.status_var.set(
                self.t("ready") if self._report is None
                else self.t("finished_status", name=self._report.output_path.name))

    # ------------------------------------------------------------------ helpers
    def _fmt(self) -> str:
        return LABEL_TO_FORMAT.get(self.format_var.get(), "docx")

    def _language(self):
        return NAME_TO_OCR_LANGUAGE.get(self.language_var.get(), DEFAULT_LANGUAGE)

    def _sync_extension(self) -> None:
        out = expand_user(self.output_var.get())
        if not out:
            return
        with contextlib.suppress(ValueError):
            # Nothing sensible to rewrite for "." or "/"; _start() asks for a real file name.
            self.output_var.set(str(with_format_suffix(Path(out), self._fmt())))

    def _pick_input(self) -> None:
        initial = expand_user(self.input_var.get()) or str(Path.home())
        chosen = filedialog.askdirectory(title=self.t("pick_input_title"), initialdir=initial)
        if chosen:
            self.input_var.set(chosen)
            if not self.output_var.get().strip():
                folder = Path(chosen)
                self.output_var.set(str(folder.parent / f"{folder.name}.{self._fmt()}"))

    def _pick_output(self) -> None:
        fmt = self._fmt()
        typed = expand_user(self.output_var.get())
        current = Path(typed) if typed else None
        chosen = filedialog.asksaveasfilename(
            title=self.t("pick_output_title"), defaultextension=f".{fmt}",
            initialdir=str(current.parent) if current else str(Path.home()),
            initialfile=current.name if current else f"result.{fmt}",
            filetypes=[(FORMATS[fmt], f"*.{fmt}"), (self.t("all_files"), "*.*")],
        )
        if chosen:
            self.output_var.set(chosen)

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _persist(self) -> None:
        _save_settings({
            "input_dir": self.input_var.get(), "output_path": self.output_var.get(),
            "format": self._fmt(), "language": self._language().code,
            "ui_language": self.ui_language,
            "file_headings": self.headings_var.get(), "page_breaks": self.page_breaks_var.get(),
        })

    def _warn(self, key: str, **fields: object) -> None:
        messagebox.showwarning(self.t("window_title"), self.t(key, **fields))

    # ------------------------------------------------------------------ actions
    def _start(self) -> None:
        folder = expand_user(self.input_var.get())
        output = expand_user(self.output_var.get())
        if not folder or not Path(folder).is_dir():
            self._warn("need_folder")
            return
        if not output:
            self._warn("need_output")
            return
        if Path(output).is_dir():
            self._warn("need_file_not_folder")
            return
        fmt = self._fmt()
        try:
            output_path = with_format_suffix(Path(output), fmt)
        except ValueError:
            self._warn("need_file_not_folder")
            return
        self.output_var.set(str(output_path))
        problem = check_writable(output_path)
        if problem:
            messagebox.showerror(self.t("window_title"),
                                 self.t("cannot_save", path=output_path, reason=problem))
            return
        if output_path.is_file() and not messagebox.askyesno(
                self.t("window_title"), self.t("overwrite", path=output_path)):
            return
        self._persist()

        job = Job(input_dir=Path(folder), output_path=output_path, fmt=fmt,
                  file_headings=self.headings_var.get(), page_breaks=self.page_breaks_var.get(),
                  lang=self._language().code)
        self._cancel.clear()
        self._report = None
        self._clear_log()
        self.progress["value"] = 0
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.open_btn.configure(state="disabled")
        self.status_var.set(self.t("starting"))
        self._worker = threading.Thread(target=self._run, args=(job,), daemon=True)
        self._worker.start()

    def _run(self, job: Job) -> None:
        q = self._events
        try:
            report = run_job(
                job,
                on_progress=lambda d, t, n: q.put(("progress", d, t, n)),
                on_log=lambda m: q.put(("log", m)),
                cancel=self._cancel,
            )
            q.put(("done", report))
        except Cancelled:
            q.put(("cancelled",))
        except Exception as exc:  # shown to the user, never swallowed
            q.put(("error", f"{exc.__class__.__name__}: {exc}"))

    def _cancel_job(self) -> None:
        self._cancel.set()
        self.status_var.set(self.t("cancelling"))
        self.cancel_btn.configure(state="disabled")

    def _open_result(self) -> None:
        if self._report is not None:
            open_path(self._report.output_path, self.ui_language)

    def _finish(self) -> None:
        self.start_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")

    def _poll(self) -> None:
        try:
            self._drain_events()
        finally:
            self.after(self.POLL_MS, self._poll)

    def _drain_events(self) -> None:
        try:
            while True:
                event = self._events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self._append_log(event[1])
                elif kind == "progress":
                    _, done, total, name = event
                    self.progress["maximum"] = max(1, total)
                    self.progress["value"] = done
                    self.status_var.set(
                        self.t("processing", done=done + 1, total=total, name=name) if name
                        else self.t("processing", done=total, total=total, name=""))
                elif kind == "done":
                    self._report = event[1]
                    self._finish()
                    self.open_btn.configure(state="normal")
                    self._show_summary(self._report)
                elif kind == "cancelled":
                    self._finish()
                    self.status_var.set(self.t("cancelled_status"))
                    self._append_log(self.t("cancelled_log"))
                elif kind == "error":
                    self._finish()
                    self.status_var.set(self.t("error_status"))
                    self._append_log("ERROR: " + event[1])
                    messagebox.showerror(self.t("window_title"), event[1])
        except queue.Empty:
            pass

    def _show_summary(self, report: Report) -> None:
        message = self.t("summary", ok=report.ok_count, total=len(report.pages),
                         path=report.output_path)
        if report.failed:
            message += self.t("summary_failed", count=len(report.failed))
        if report.empty:
            message += self.t("summary_empty", count=len(report.empty))
        if report.low_confidence:
            message += self.t("summary_low_confidence", count=len(report.low_confidence))
        self.status_var.set(self.t("finished_status", name=report.output_path.name))
        if messagebox.askyesno(self.t("window_title"), message + self.t("summary_open")):
            open_path(report.output_path, self.ui_language)

    def _on_close(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            if not messagebox.askyesno(self.t("window_title"), self.t("quit_while_running")):
                return
            self._cancel.set()
        self._persist()
        self.destroy()


def main() -> None:
    if sys.platform == "win32":  # crisp text on high-DPI displays
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        except Exception:
            pass
    app = App()
    app.mainloop()
