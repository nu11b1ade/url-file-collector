#!/usr/bin/env python3
"""中文 tkinter 界面；运行时仅使用 Python 标准库。"""

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from collect_url_files_core import CollectionProgress, collect


class _WindowClosed(Exception):
    """Stop the worker between files when its window has closed."""


class CollectorApp:
    def __init__(self, root):
        self.root = root
        self.running = False
        self._events = queue.Queue(maxsize=256)
        self._closing = threading.Event()
        self._preview = False
        self.source = tk.StringVar()
        self.output = tk.StringVar()
        self.preview = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="就绪，请选择源文件夹和输出文件夹。")
        self.counts = tk.StringVar()
        self._controls = []

        root.title("网址文件收集器")
        root.geometry("900x600")
        root.minsize(700, 420)
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        frame = ttk.Frame(root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(6, weight=1)

        for row, label, variable, mustexist in (
            (0, "源文件夹", self.source, True),
            (1, "输出文件夹", self.output, False),
        ):
            ttk.Label(frame, text=label).grid(row=row, column=0, padx=(0, 10), pady=5)
            entry = ttk.Entry(frame, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            browse = ttk.Button(
                frame, text="浏览…",
                command=lambda var=variable, title=label, exists=mustexist:
                    self._browse(var, title, exists),
            )
            browse.grid(row=row, column=2, padx=(10, 0), pady=5)
            self._controls.extend((entry, browse))

        preview = ttk.Checkbutton(frame, text="仅预览(不复制)", variable=self.preview)
        preview.grid(row=2, column=0, columnspan=2, sticky="w", pady=10)
        self.start_button = ttk.Button(frame, text="开始", command=self.start)
        self.start_button.grid(row=2, column=2, sticky="e", pady=10)
        self._controls.extend((preview, self.start_button))

        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        ttk.Label(frame, textvariable=self.counts).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(0, 5)
        )
        ttk.Label(frame, textvariable=self.status, wraplength=660).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )

        hits_frame = ttk.LabelFrame(frame, text="命中文件（相对路径：源 → 输出）", padding=5)
        hits_frame.grid(row=6, column=0, columnspan=3, sticky="nsew")
        hits_frame.columnconfigure(0, weight=1)
        hits_frame.rowconfigure(0, weight=1)
        self.hit_list = tk.Listbox(hits_frame, selectmode="extended", exportselection=False)
        self.hit_list.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(hits_frame, orient="vertical", command=self.hit_list.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(hits_frame, orient="horizontal", command=self.hit_list.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.hit_list.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self._show_progress(CollectionProgress())

    def _browse(self, variable, title, mustexist):
        folder = filedialog.askdirectory(
            parent=self.root, title="选择" + title, mustexist=mustexist
        )
        if folder:
            variable.set(folder)

    def start(self):
        if self.running:
            return
        source, output = self.source.get().strip(), self.output.get().strip()
        try:
            if not source or not output:
                raise ValueError("请选择源文件夹和输出文件夹。")
            src = Path(source).expanduser().resolve()
            out = Path(output).expanduser().resolve()
            if not src.is_dir():
                raise ValueError("源文件夹不存在或不是文件夹。")
            if src == out:
                raise ValueError("源文件夹和输出文件夹不能相同。")
            if out.exists() and not out.is_dir():
                raise ValueError("输出路径已经存在，但不是文件夹。")
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showerror("无法开始", str(error), parent=self.root)
            return

        self.running = True
        self._preview = self.preview.get()
        for control in self._controls:
            control.state(["disabled"])
        self.hit_list.delete(0, tk.END)
        self._show_progress(CollectionProgress())
        self.status.set("正在预览（不复制）…" if self._preview else "正在扫描并复制…")
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)
        # Only the main thread touches Tk. The worker publishes plain events.
        worker = threading.Thread(target=self._scan, args=(src, out, self._preview))
        worker.start()
        self.root.after(40, self._poll)

    def _post(self, kind, value):
        # Bound queued events so very large scans cannot outrun the interface.
        while not self._closing.is_set():
            try:
                self._events.put((kind, value), timeout=0.1)
                return
            except queue.Full:
                continue
        raise _WindowClosed()

    def _scan(self, src, out, preview):
        try:
            result = collect(
                src, out, dry_run=preview,
                on_event=lambda event: self._post("file", event),
            )
        except _WindowClosed:
            return
        except Exception as error:
            kind, value = "error", str(error)
        else:
            kind, value = "done", result
        try:
            self._post(kind, value)
        except _WindowClosed:
            pass

    def _poll(self):
        # Limit each batch to leave time for repainting, scrolling, and closing.
        for _ in range(100):
            try:
                kind, value = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "file":
                self._show_progress(value.progress)
                prefix = "正在预览：" if self._preview else "正在扫描："
                self.status.set(prefix + str(value.relative_path))
                if value.destination is not None:
                    action = {
                        "copied": "已复制",
                        "would copy": "预览",
                        "same content; skipped": "相同内容，跳过",
                    }[value.action]
                    self.hit_list.insert(tk.END, "{} → {} [{}]".format(
                        value.relative_path, value.destination, action
                    ))
            else:
                self.running = False
                self.progress.stop()
                self.progress.configure(
                    mode="determinate", maximum=1, value=1 if kind == "done" else 0
                )
                for control in self._controls:
                    control.state(["!disabled"])
                if kind == "done":
                    self._show_progress(value)
                    self.status.set("预览完成（未复制文件）。" if self._preview else "收集完成。")
                else:
                    self.status.set("收集失败：" + value)
                    messagebox.showerror("收集失败", value, parent=self.root)
                return
        if self.running:
            self.root.after(40, self._poll)

    def _show_progress(self, progress):
        self.counts.set("扫描：{}    命中：{}    复制：{}    跳过：{}".format(
            progress.scanned, progress.hits, progress.copied, progress.skipped
        ))

    def _close(self):
        # A non-daemon worker finishes its current file, then exits at _post().
        self._closing.set()
        self.root.destroy()


def main():
    root = tk.Tk()
    CollectorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
