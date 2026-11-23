"""Tkinter front-end: GOTV demo-folder watcher (default) and manual .dem mode."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import traceback
import webbrowser
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, Toplevel, filedialog, messagebox, ttk
from tkinter import END, N, S, E, W

from .app import describe_failure, process_demo
from .config import APP_NAME, APP_VERSION, TARGET_DEFAULT
from .demo_parser import ParseError
from .demo_watch import DemoWatcher
from .first_run import (
    StatSetupResult,
    detect_cfg_dir,
    ensure_local_stat_cfg,
    install_stat_cfg,
)
from .gotv_config import (
    GOTV_CONFIG_NAME,
    default_demo_dir,
    install_config as install_gotv_config,
    write_config as write_gotv_config,
)
from .gsi_config import find_cs2_cfg_dirs
from .logging_setup import configure_logging, log_file
from .paths import load_settings, output_dir as app_output_dir, save_settings
from .validation import ValidationError, ValidationFailed

LOGGER = logging.getLogger(__name__)


BG = "#151a20"
PANEL = "#1f2731"
FIELD = "#10151b"
TEXT = "#e8eef4"
MUTED = "#93a5b5"
ORANGE = "#ffa028"
ORANGE_DARK = "#cc7a00"
LINE = "#31404f"


def _set_theme(root: Tk) -> None:
    style = ttk.Style(root)
    for theme in ("clam", "vista", "default"):
        try:
            style.theme_use(theme)
            break
        except Exception:
            continue
    root.configure(bg=BG)
    body = ("Microsoft YaHei UI", 13)
    style.configure(".", font=body, background=BG, foreground=TEXT)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure(
        "TButton",
        font=body,
        padding=(14, 9),
        background=PANEL,
        foreground=TEXT,
        bordercolor=LINE,
        focuscolor=ORANGE,
    )
    style.map(
        "TButton",
        background=[("disabled", "#1a2129"), ("pressed", ORANGE_DARK), ("active", "#2a3644")],
        foreground=[("disabled", "#5c6b7a")],
    )
    style.configure(
        "Go.TButton",
        font=("Microsoft YaHei UI", 15, "bold"),
        padding=(18, 11),
        background=ORANGE,
        foreground="#1a1206",
    )
    style.map(
        "Go.TButton",
        background=[("disabled", "#6b5a3a"), ("pressed", ORANGE_DARK), ("active", "#ffb84d")],
        foreground=[("disabled", "#2c2617")],
    )
    style.configure(
        "TEntry",
        font=body,
        padding=6,
        fieldbackground=FIELD,
        foreground=TEXT,
        bordercolor=LINE,
        insertcolor=TEXT,
    )
    style.configure("TCheckbutton", font=body, background=BG, foreground=TEXT, padding=(2, 4))
    style.map(
        "TCheckbutton",
        background=[("active", BG)],
        foreground=[("active", "#ffffff")],
    )
    style.configure("TNotebook", background=BG, bordercolor=LINE)
    style.configure("TNotebook.Tab", font=body, padding=(16, 10), background=PANEL, foreground=MUTED)
    style.map(
        "TNotebook.Tab",
        background=[("selected", "#2a3644")],
        foreground=[("selected", "#ffffff")],
    )
    style.configure("TSeparator", background=LINE)
    style.configure("TProgressbar", background=ORANGE, troughcolor=FIELD, bordercolor=LINE)
    style.configure("Title.TLabel", font=("Microsoft YaHei UI", 26, "bold"), foreground="#ffffff")
    style.configure("Accent.TLabel", font=("Microsoft YaHei UI", 13, "bold"), foreground=ORANGE)
    style.configure("Sub.TLabel", font=body, foreground=MUTED)
    style.configure("Field.TLabel", font=body, foreground="#c9d6e2")
    style.configure("Hint.TLabel", font=("Microsoft YaHei UI", 11), foreground=MUTED)
    style.configure("Live.TLabel", font=("Microsoft YaHei UI", 12, "bold"), foreground=ORANGE)


def _dpi_aware() -> None:
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class DemoWatchPanel(ttk.Frame):
    def __init__(self, master) -> None:
        super().__init__(master, padding=(22, 18))
        settings = load_settings()
        saved = Path(settings["replays_dir"]) if settings.get("replays_dir") else None
        detected = saved if saved and saved.exists() else default_demo_dir()
        self.watch_var = StringVar(value=str(detected) if detected else "")
        app_output_dir().mkdir(parents=True, exist_ok=True)
        app_output_dir().mkdir(parents=True, exist_ok=True)
        self.out_var = StringVar(value=str(app_output_dir()))
        self.target_var = StringVar(value=TARGET_DEFAULT)
        self.fixture_var = StringVar(value="")
        self.gotv_status_var = StringVar(value="")
        self.stat_status_var = StringVar(value="")
        self.status_var = StringVar(value="选择 demo 文件夹后点击“开始监听”。")
        self.open_after_var = BooleanVar(value=True)
        self.allow_missing_var = BooleanVar(value=True)
        self.watcher: DemoWatcher | None = None
        self.last_report: Path | None = None
        self.queue: queue.Queue = queue.Queue()
        self._build()
        self.refresh_gotv_status()
        self.refresh_stat_status()
        self.after(120, self._poll_queue)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        self.columnconfigure(1, weight=1)

        ttk.Label(self, text="自动监听 Demo 文件夹", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky=W, pady=(0, 12)
        )

        ttk.Label(self, text="Demo 输出文件夹", style="Field.TLabel").grid(row=1, column=0, sticky=W, pady=6)
        ttk.Entry(self, textvariable=self.watch_var).grid(
            row=1, column=1, sticky=(E, W), padx=(12, 8), pady=6
        )
        ttk.Button(self, text="浏览…", command=self._choose_watch).grid(row=1, column=2, sticky=E, pady=6)

        ttk.Label(self, text="报告输出目录（固定）", style="Field.TLabel").grid(row=2, column=0, sticky=W, pady=6)
        ttk.Entry(self, textvariable=self.out_var, state="readonly").grid(
            row=2, column=1, sticky=(E, W), padx=(12, 8), pady=6
        )
        ttk.Button(self, text="打开", command=self._open_output_dir).grid(row=2, column=2, sticky=E, pady=6)

        ttk.Label(self, text="目标选手", style="Field.TLabel").grid(row=3, column=0, sticky=W, pady=6)
        ttk.Entry(self, textvariable=self.target_var, width=28).grid(
            row=3, column=1, columnspan=2, sticky=W, padx=(12, 8), pady=6
        )

        ttk.Label(self, text="计分板 fixture", style="Field.TLabel").grid(row=4, column=0, sticky=W, pady=6)
        ttk.Entry(self, textvariable=self.fixture_var).grid(
            row=4, column=1, sticky=(E, W), padx=(12, 8), pady=6
        )
        ttk.Button(self, text="浏览…", command=self._choose_fixture).grid(row=4, column=2, sticky=E, pady=6)

        ttk.Checkbutton(
            self,
            text="没有 fixture 时也生成",
            variable=self.allow_missing_var,
        ).grid(row=5, column=1, columnspan=2, sticky=W, padx=(12, 0), pady=(4, 0))

        ttk.Separator(self).grid(row=6, column=0, columnspan=3, sticky=(E, W), pady=14)

        buttons = ttk.Frame(self)
        buttons.grid(row=7, column=0, columnspan=3, sticky=(E, W))
        self.start_button = ttk.Button(buttons, text="开始监听", style="Go.TButton", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="停止监听", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        self.open_button = ttk.Button(buttons, text="打开最近报告", command=self._open_latest, state="disabled")
        self.open_button.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="高级 / GOTV 配置", command=self._open_gotv_dialog).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(buttons, text="生成后自动打开", variable=self.open_after_var).pack(side="right")

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100, value=0)
        self.progress.grid(row=8, column=0, columnspan=3, sticky=(E, W), pady=(12, 4))
        ttk.Label(self, textvariable=self.status_var, style="Field.TLabel", wraplength=880).grid(
            row=9, column=0, columnspan=3, sticky=W, pady=(2, 0)
        )

        stat_row = ttk.Frame(self)
        stat_row.grid(row=10, column=0, columnspan=3, sticky=(E, W), pady=(10, 0))
        ttk.Button(stat_row, text="配置 stats.cfg…", command=self._manual_stat_setup).pack(side="left")
        ttk.Label(
            stat_row,
            textvariable=self.stat_status_var,
            style="Hint.TLabel",
            wraplength=680,
            justify="left",
        ).pack(side="left", padx=(10, 0))

    def _open_gotv_dialog(self) -> None:
        win = Toplevel(self)
        win.title("GOTV 配置（高级）")
        win.configure(bg=BG)
        win.transient(self.winfo_toplevel())
        win.grab_set()
        win.geometry("640x300")
        body = ttk.Frame(win, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text="GOTV 配置", style="Title.TLabel").grid(row=0, column=0, sticky=W)
        ttk.Label(body, textvariable=self.gotv_status_var, style="Field.TLabel", wraplength=590).grid(
            row=1, column=0, sticky=W, pady=(10, 8)
        )
        row = ttk.Frame(body)
        row.grid(row=2, column=0, sticky=W, pady=(0, 10))
        ttk.Button(row, text="生成 GOTV 配置", command=self._install_gotv).pack(side="left")
        ttk.Button(row, text="打开 cfg 目录", command=self._open_cfg_dir).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="刷新", command=self.refresh_gotv_status).pack(side="left", padx=(8, 0))
        ttk.Label(
            body,
            text=(
                "开图前在 CS2 控制台执行：exec cs2stats_gotv\n"
                "然后：map de_mirage\n\n"
                "等价命令：tv_enable 1; tv_autorecord 1; map de_mirage\n"
                "中途再设置 tv_enable 不会对本局生效。"
            ),
            style="Hint.TLabel",
            wraplength=590,
            justify="left",
        ).grid(row=3, column=0, sticky=W)
        ttk.Button(body, text="关闭", command=win.destroy).grid(row=4, column=0, sticky=E, pady=(12, 0))
        win.wait_window()

    # ------------------------------------------------------------------
    def refresh_gotv_status(self) -> None:
        dirs = find_cs2_cfg_dirs()
        if not dirs:
            self.gotv_status_var.set("未检测到 CS2 cfg 目录。可手动生成配置并放到 game\\csgo\\cfg。")
            return
        target = dirs[0] / GOTV_CONFIG_NAME
        if target.is_file():
            self.gotv_status_var.set(f"GOTV 配置已生成：{target}")
        else:
            self.gotv_status_var.set(f"GOTV 配置未生成。将写入：{target}")

    def _install_gotv(self) -> None:
        try:
            path = install_gotv_config()
            self.gotv_status_var.set(f"GOTV 配置已写入：{path}")
            messagebox.showinfo(
                "GOTV 配置已生成",
                f"已写入：\n{path}\n\n开图前在 CS2 控制台执行：\n"
                "exec cs2stats_gotv\nmap de_mirage\n\n"
                "tv_enable 必须在 map 之前；中途设置无效。",
            )
        except FileNotFoundError:
            path = write_gotv_config()
            self.gotv_status_var.set(f"未找到 CS2；已生成到：{path}")
            messagebox.showwarning(
                "未找到 CS2 目录",
                f"没有找到 game\\csgo\\cfg，已生成到：\n{path}\n\n"
                "请手动复制到 <CS2>\\game\\csgo\\cfg\\，再在控制台 exec cs2stats_gotv。",
            )
        except Exception as exc:
            messagebox.showerror("生成失败", str(exc))

    def _open_cfg_dir(self) -> None:
        dirs = find_cs2_cfg_dirs()
        if not dirs:
            messagebox.showwarning("未找到 cfg 目录", "没有检测到 CS2 的 game\\csgo\\cfg 目录。")
            return
        try:
            os.startfile(str(dirs[0]))  # type: ignore[attr-defined]
        except Exception:
            subprocess.Popen(["explorer", str(dirs[0])])

    def _choose_watch(self) -> None:
        path = filedialog.askdirectory(title="选择 GOTV demo 输出文件夹（game/csgo/replays）")
        if path:
            self.watch_var.set(path)
            settings = load_settings()
            settings["replays_dir"] = path
            save_settings(settings)

    def _open_output_dir(self) -> None:
        path = app_output_dir()
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception:
            subprocess.Popen(["explorer", str(path)])

    def refresh_stat_status(self) -> None:
        settings = load_settings()
        target = settings.get("stat_cfg_target") or ""
        command = settings.get("stat_cfg_command") or ""
        if target and command:
            self.stat_status_var.set(
                f"stats.cfg 已就绪：{target}\n开图前在控制台执行：{command}"
            )
        else:
            self.stat_status_var.set(
                "stats.cfg 尚未复制到 CS2：点击左侧按钮选择 game\\csgo\\cfg。"
            )

    def _manual_stat_setup(self) -> None:
        path = filedialog.askdirectory(
            title="选择 CS2 的 game\\csgo\\cfg 目录",
            mustexist=True,
        )
        if not path:
            return
        try:
            result = install_stat_cfg(path)
        except Exception as exc:
            messagebox.showerror("stats.cfg 配置失败", str(exc))
            return
        self._remember_stat_setup(result)
        self.refresh_stat_status()
        messagebox.showinfo(
            "stats.cfg 已配置",
            f"{result.message}\n\n开图前在 CS2 控制台执行：\n{result.command}",
        )

    def _remember_stat_setup(self, result: StatSetupResult) -> None:
        settings = load_settings()
        settings.update(
            {
                "stat_cfg_target": str(result.target_path) if result.target_path else "",
                "stat_cfg_command": result.command or "",
                "cfg_dir": str(result.cfg_dir) if result.cfg_dir else "",
            }
        )
        save_settings(settings)

    def _choose_fixture(self) -> None:
        path = filedialog.askopenfilename(
            title="选择计分板 fixture",
            filetypes=[("Fixture JSON", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self.fixture_var.set(path)

    # ------------------------------------------------------------------
    def _start(self) -> None:
        if self.watcher is not None and self.watcher.running:
            return
        watch = self.watch_var.get().strip().strip('"')
        if not watch:
            messagebox.showwarning(
                "缺少目录",
                "没有找到 CS2 的 game\\csgo\\replays。\n"
                "请点击“浏览…”选择，或先开一张地图让它自动创建。",
            )
            return
        if not Path(watch).is_dir():
            messagebox.showwarning(
                "replays 目录尚不存在",
                f"找不到：\n{watch}\n\n"
                "首次开图后该目录会自动创建；也可以点击“浏览…”选择其他 demo 文件夹。",
            )
            return
        settings = load_settings()
        settings["replays_dir"] = watch
        save_settings(settings)
        out = self.out_var.get().strip().strip('"') or str(app_output_dir())
        target = self.target_var.get().strip() or TARGET_DEFAULT
        fixture = self.fixture_var.get().strip().strip('"') or None
        try:
            watcher = DemoWatcher(
                watch,
                out_dir=out,
                target=target,
                fixture_path=fixture,
                allow_missing_fixture=self.allow_missing_var.get(),
                open_after=self.open_after_var.get(),
                on_status=lambda message: self.queue.put(("status", message)),
                on_report=lambda result: self.queue.put(("report", result)),
                on_error=lambda message: self.queue.put(("error", message)),
            )
            watcher.start()
        except Exception as exc:
            LOGGER.exception("watcher start failed")
            messagebox.showerror("无法开始监听", f"{exc}\n\n日志：{log_file()}")
            return

        self.watcher = watcher
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set(f"正在监听：{watch}\n等待新的 .dem（启动时只处理最新一个）。")

    def _stop(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
        self.watcher = None
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.status_var.set("已停止监听。")

    def _open_latest(self) -> None:
        if self.last_report is None or not self.last_report.is_file():
            messagebox.showinfo("还没有报告", "还没有生成报告。")
            return
        try:
            webbrowser.open(self.last_report.resolve().as_uri())
        except Exception:
            messagebox.showerror("打开失败", f"无法打开：\n{self.last_report}")

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "report":
                    self._finish_report(payload)
                elif kind == "error":
                    self._show_watch_error(str(payload))
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)

    def _finish_report(self, result) -> None:
        self.last_report = result.report_path
        self.open_button.configure(state="normal")
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set(f"报告已生成：{result.report_path}\n继续监听下一场…")
        if not result.target_found:
            messagebox.showwarning(
                "目标选手未找到",
                f"没有找到 {self.target_var.get()!r}，已回退为全场 Rating 最高选手"
                f"（{result.target_name or '未知'}）。",
            )

    def _show_watch_error(self, message: str) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.status_var.set("这个 demo 处理失败；监听仍在继续。")
        messagebox.showerror("demo 处理失败", f"{message}\n\n日志：{log_file()}")


class DemoPanel(ttk.Frame):
    def __init__(self, master) -> None:
        super().__init__(master, padding=(22, 18))
        self.demo_var = StringVar()
        self.target_var = StringVar(value=TARGET_DEFAULT)
        self.fixture_var = StringVar()
        self.out_var = StringVar(value=str(app_output_dir()))
        self.status_var = StringVar(value="选择一个 .dem 文件开始。")
        self.open_after_var = BooleanVar(value=True)
        self.running = False
        self.queue: queue.Queue = queue.Queue()
        self._build()
        self.after(120, self._poll_queue)

    def _build(self) -> None:
        self.columnconfigure(1, weight=1)

        ttk.Label(self, text="CS2 Demo 数据报告", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky=W
        )
        ttk.Label(
            self,
            text="选择 .dem，报告统一生成到 exe 同级的 output 文件夹。",
            style="Sub.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky=W, pady=(4, 18))

        ttk.Label(self, text="Demo 文件", style="Field.TLabel").grid(row=2, column=0, sticky=W, pady=4)
        ttk.Entry(self, textvariable=self.demo_var).grid(
            row=2, column=1, sticky=(E, W), padx=(12, 8), pady=4
        )
        ttk.Button(self, text="浏览…", command=self._choose_demo).grid(row=2, column=2, sticky=E, pady=4)

        ttk.Label(self, text="目标选手", style="Field.TLabel").grid(row=3, column=0, sticky=W, pady=4)
        ttk.Entry(self, textvariable=self.target_var, width=28).grid(
            row=3, column=1, columnspan=2, sticky=W, padx=(12, 8), pady=4
        )
        ttk.Label(
            self,
            text="用于 Player performance overview；找不到时回退为全场 Rating 最高选手。",
            style="Hint.TLabel",
        ).grid(row=4, column=1, columnspan=2, sticky=W, padx=(12, 0))

        ttk.Label(self, text="计分板 fixture", style="Field.TLabel").grid(row=5, column=0, sticky=W, pady=4)
        ttk.Entry(self, textvariable=self.fixture_var).grid(
            row=5, column=1, sticky=(E, W), padx=(12, 8), pady=4
        )
        ttk.Button(self, text="浏览…", command=self._choose_fixture).grid(row=5, column=2, sticky=E, pady=4)
        ttk.Label(
            self,
            text="留空则按 demo 的 SHA-256 在 fixtures/ 中自动查找；严格模式要求校验通过。",
            style="Hint.TLabel",
        ).grid(row=6, column=1, columnspan=2, sticky=W, padx=(12, 0))

        ttk.Label(self, text="报告输出目录（固定）", style="Field.TLabel").grid(row=7, column=0, sticky=W, pady=4)
        ttk.Entry(self, textvariable=self.out_var, state="readonly").grid(
            row=7, column=1, columnspan=2, sticky=(E, W), padx=(12, 0), pady=4
        )

        ttk.Separator(self).grid(row=8, column=0, columnspan=3, sticky=(E, W), pady=14)

        self.go_button = ttk.Button(self, text="开始生成报告", style="Go.TButton", command=self._start)
        self.go_button.grid(row=9, column=1, sticky=W, padx=(12, 0), pady=(0, 6))
        ttk.Checkbutton(self, text="生成后打开报告", variable=self.open_after_var).grid(
            row=9, column=2, sticky=E, pady=(0, 6)
        )

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.grid(row=10, column=0, columnspan=3, sticky=(E, W), pady=(8, 4))
        ttk.Label(self, textvariable=self.status_var, style="Field.TLabel", wraplength=760).grid(
            row=11, column=0, columnspan=3, sticky=W, pady=(2, 0)
        )

        ttk.Label(
            self,
            text=(
                "精确层：K/D/A、ADR、K (HS) 来自 demoparser2 计分板属性，生成前逐项对照 fixture；"
                "任何差异都会停止生成。KAST 为推断，Open Kills 为参考字段；"
                "Kill Matrix 只覆盖逐回合核对一致的回合；Rating / Swing 为近似值。"
            ),
            style="Hint.TLabel",
            wraplength=760,
        ).grid(row=12, column=0, columnspan=3, sticky=W, pady=(18, 0))

    def _choose_demo(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 CS2 demo",
            filetypes=[("CS2 demo", "*.dem"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self.demo_var.set(path)
        self.status_var.set("已选择 demo；报告将写入 exe 同级的 output 文件夹。")

    def _choose_fixture(self) -> None:
        path = filedialog.askopenfilename(
            title="选择计分板 fixture",
            filetypes=[("Fixture JSON", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self.fixture_var.set(path)

    def _start(self) -> None:
        if self.running:
            return
        demo = self.demo_var.get().strip().strip('"')
        if not demo:
            messagebox.showwarning("缺少 demo", "请先选择 .dem 文件。")
            return
        if not Path(demo).is_file():
            messagebox.showerror("文件不存在", f"找不到：\n{demo}")
            return
        target = self.target_var.get().strip() or TARGET_DEFAULT
        fixture = self.fixture_var.get().strip().strip('"') or None

        self.running = True
        self.go_button.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set(f"开始解析……（目标选手：{target}）")

        def worker() -> None:
            try:
                result = process_demo(
                    demo,
                    out_dir=self.out_var.get().strip() or None,
                    target=target,
                    fixture_path=fixture,
                    progress=lambda stage, message: self.queue.put(("progress", message)),
                )
                self.queue.put(("done", result))
            except ValidationFailed as exc:
                self.queue.put(("validation_failed", exc.result))
            except (ParseError, ValidationError) as exc:
                self.queue.put(("error", str(exc)))
            except Exception:
                LOGGER.exception("unexpected GUI error")
                self.queue.put(("error", traceback.format_exc()))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "progress":
                    self.status_var.set(str(payload))
                elif kind == "done":
                    self._finish_success(payload)
                elif kind == "validation_failed":
                    self._finish_validation_failed(payload)
                elif kind == "error":
                    self._finish_error(str(payload))
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)

    def _stop_progress(self) -> None:
        self.running = False
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.go_button.configure(state="normal")

    def _finish_success(self, result) -> None:
        self._stop_progress()
        self.status_var.set(f"完成：{result.report_path}")
        if not result.target_found:
            messagebox.showwarning(
                "目标选手未找到",
                f"没有找到选手 {self.target_var.get()!r}，报告已回退为全场 Rating 最高选手"
                f"（{result.target_name or '未知'}）。",
            )
        if self.open_after_var.get():
            try:
                webbrowser.open(result.report_path.resolve().as_uri())
            except Exception:
                LOGGER.warning("could not open browser", exc_info=True)
        messagebox.showinfo(
            "报告已生成",
            f"报告已生成：\n{result.report_path}\n\n校验状态：{result.validation.status}",
        )

    def _finish_validation_failed(self, result) -> None:
        self._stop_progress()
        self.status_var.set(f"已停止：{result.title}")
        messagebox.showerror(
            "精确字段校验未通过 — 未生成 HTML",
            describe_failure(result) + f"\n\n日志：{log_file()}",
        )

    def _finish_error(self, message: str) -> None:
        self._stop_progress()
        self.status_var.set("出错，未生成报告。")
        messagebox.showerror("生成失败", f"{message}\n\n日志：{log_file()}")


class AppWindow:
    def __init__(self) -> None:
        self.root = Tk()
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("1040x820")
        self.root.minsize(940, 760)
        _set_theme(self.root)
        _dpi_aware()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self._first_run_setup)

    def _build(self) -> None:
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        self.watcher = DemoWatchPanel(notebook)
        self.demo = DemoPanel(notebook)
        notebook.add(self.watcher, text="自动监听 Demo（默认）")
        notebook.add(self.demo, text="手动选择 Demo")
        notebook.select(0)

    def _first_run_setup(self) -> None:
        settings = load_settings()
        ensure_local_stat_cfg()
        if settings.get("first_run_done"):
            self._migrate_legacy_stat_cfg(settings)
            self.watcher.refresh_stat_status()
            return
        cfg_dir = detect_cfg_dir()
        if cfg_dir is not None:
            try:
                result = install_stat_cfg(cfg_dir)
            except FileExistsError:
                cfg_dir = None
            except Exception:
                LOGGER.exception("first-run stats.cfg copy failed")
                cfg_dir = None
            else:
                self._remember_stat_setup(result)
                settings = load_settings()
                settings["first_run_done"] = True
                save_settings(settings)
                self.watcher.refresh_stat_status()
                messagebox.showinfo(
                    "stats.cfg 已生成",
                    f"{result.message}\n\n开图前在 CS2 控制台执行：\n{result.command}\n\n"
                    "然后：map de_mirage",
                )
                return

        if cfg_dir is None:
            path = filedialog.askdirectory(
                title="未自动找到 CS2 cfg 目录，请选择 game\\csgo\\cfg（可取消）",
                mustexist=True,
            )
            if path:
                try:
                    result = install_stat_cfg(path)
                except Exception as exc:
                    messagebox.showwarning("stats.cfg 未复制", str(exc))
                else:
                    self._remember_stat_setup(result)
                    settings = load_settings()
                    settings["first_run_done"] = True
                    save_settings(settings)
                    self.watcher.refresh_stat_status()
                    messagebox.showinfo(
                        "stats.cfg 已配置",
                        f"{result.message}\n\n开图前执行：{result.command}",
                    )
                    return
        settings = load_settings()
        settings["first_run_done"] = True
        save_settings(settings)
        self.watcher.refresh_stat_status()
        messagebox.showwarning(
            "stats.cfg 未自动复制",
            "已在 exe 同级生成 stats.cfg。\n\n"
            "请手动复制到 <CS2>\\game\\csgo\\cfg\\，或在主界面点击“配置 stats.cfg…”选择目录。\n"
            "开图前在控制台执行：exec stats",
        )

    def _migrate_legacy_stat_cfg(self, settings: dict) -> None:
        """Older builds installed stat.cfg / stat_cs2stats.cfg; move to stats.cfg."""

        cfg_dir = settings.get("cfg_dir") or ""
        if not cfg_dir:
            return
        directory = Path(cfg_dir)
        if not directory.is_dir():
            return
        legacy = [
            directory / name
            for name in ("stat_cs2stats.cfg", "stat.cfg")
            if (directory / name).is_file()
        ]
        target = directory / "stats.cfg"
        command = settings.get("stat_cfg_command") or ""
        if not legacy and target.is_file() and command == "exec stats":
            return
        try:
            result = install_stat_cfg(directory)
        except Exception:
            LOGGER.exception("legacy stat cfg migration failed")
            return
        self._remember_stat_setup(result)

    def _remember_stat_setup(self, result: StatSetupResult) -> None:
        self.watcher._remember_stat_setup(result)

    def _on_close(self) -> None:
        watcher = self.watcher
        if watcher.watcher is not None and watcher.watcher.running:
            if not messagebox.askyesno("正在监听", "Demo 监听仍在运行，确定退出吗？"):
                return
            watcher._stop()
        if self.demo.running:
            if not messagebox.askyesno("正在生成", "Demo 任务仍在运行，确定退出吗？"):
                return
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    configure_logging()
    LOGGER.info("GUI start")
    try:
        AppWindow().run()
    except Exception:
        LOGGER.exception("GUI crashed")
        try:
            messagebox.showerror(
                "启动失败",
                f"程序启动失败：\n{traceback.format_exc()}\n\n日志：{log_file()}",
            )
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
