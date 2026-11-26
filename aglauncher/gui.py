"""图形界面（Claude 经典暖纸质感主题，轻量高性能绘制）。

三块状态卡对应三条通道（代理端点 / 登录端点 / 模型端点），
残留实例单独用提示条拦在启动按钮前面——绝不在有旧实例在跑时假装启动成功。
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox
from typing import Callable

from . import __version__, core, procs, proxy, store
from .launcher import (
    build_plan,
    build_proxy_commands,
    describe_argv,
    format_preview,
    open_proxy_terminal,
    spawn,
    verify_injection,
)

# 配色：Claude 经典暖纸米白 + 陶土橙点缀
BG = "#faf9f5"
PANEL = "#ffffff"
PANEL_2 = "#f0eee6"
PANEL_HOVER = "#e5e1d5"
BORDER = "#e3dfd3"
BORDER_FOCUS = "#d97757"
TEXT = "#1f1e1d"
MUTED = "#6b675f"
DIM = "#9c968b"

ACCENT = "#d97757"
ACCENT_HOVER = "#c66546"
ACCENT_SOFT = "#fbf0ec"

GREEN = "#2a7b56"
GREEN_SOFT = "#eaf5ef"
AMBER = "#b26b09"
AMBER_SOFT = "#fdf3e3"
RED = "#c53b3b"
RED_HOVER = "#b03232"
RED_SOFT = "#fcecec"
RED_BORDER = "#e7bcbc"


def _font_families() -> tuple[str, str, str]:
    if sys.platform.startswith("win"):
        return "Microsoft YaHei UI", "Georgia", "Consolas"
    if sys.platform == "darwin":
        return "PingFang SC", "Georgia", "Menlo"
    return "Sans", "Serif", "Monospace"


_UI, _SERIF, _MONO = _font_families()
FONT = (_UI, 10)
FONT_SM = (_UI, 9)
FONT_TINY = (_UI, 8)
FONT_TITLE = (_SERIF, 17, "bold")
FONT_CARD_VAL = (_UI, 14, "bold")
FONT_MONO = (_MONO, 9)
FONT_BTN = (_UI, 10, "bold")


def _bind_hover(btn: tk.Button, normal_bg: str, hover_bg: str) -> None:
    def on_enter(_event: tk.Event) -> None:
        if str(btn.cget("state")) != "disabled":
            btn.configure(bg=hover_bg)

    def on_leave(_event: tk.Event) -> None:
        if str(btn.cget("state")) != "disabled":
            btn.configure(bg=normal_bg)

    btn.bind("<Enter>", on_enter, add="+")
    btn.bind("<Leave>", on_leave, add="+")


def _rounded_rect_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list[float]:
    r = max(2.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x1 + r, y1,
        x2 - r, y1, x2 - r, y1,
        x2, y1, x2, y1 + r, x2, y1 + r,
        x2, y2 - r, x2, y2 - r,
        x2, y2, x2 - r, y2, x2 - r, y2,
        x1 + r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y2 - r,
        x1, y1 + r, x1, y1 + r,
        x1, y1,
    ]


class Card(tk.Canvas):
    """单 HWND 矢量圆角卡片，避免多层嵌套子窗口导致拖拽缩放卡顿。"""

    def __init__(self, master: tk.Misc, title: str, subtitle: str) -> None:
        super().__init__(
            master, bg=BG, height=144, highlightthickness=0, bd=0
        )
        self._title = title
        self._subtitle = subtitle.replace("\n", " · ")
        self._status_text = "待检测"
        self._detail_text = ""
        self._color = DIM
        self._badge_text = "待检测"
        self._badge_bg = PANEL_2
        self._size: tuple[int, int] = (0, 0)
        self._badge_font = tkfont.Font(font=FONT_TINY)
        self.bind("<Configure>", self._on_configure)

    def set_state(
        self,
        text: str,
        detail: str,
        color: str,
        badge_text: str | None = None,
        badge_bg: str | None = None,
    ) -> None:
        if badge_bg is None:
            badge_bg = {
                GREEN: GREEN_SOFT,
                RED: RED_SOFT,
                AMBER: AMBER_SOFT,
                ACCENT: ACCENT_SOFT,
            }.get(color, PANEL_2)
        pill_text = badge_text or text
        if len(pill_text) > 10:
            pill_text = "已就绪" if color == GREEN else pill_text[:10]

        self._status_text = text
        self._detail_text = detail
        self._color = color
        self._badge_text = pill_text
        self._badge_bg = badge_bg
        self._redraw()

    def _on_configure(self, event: tk.Event) -> None:
        size = (event.width, event.height)
        if size != self._size:
            self._size = size
            self._redraw()

    def _redraw(self) -> None:
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 20 or h < 20:
            return
        self.delete("all")

        # 圆角白底卡片 + 暖石细边框
        self.create_polygon(
            _rounded_rect_points(1, 1, w - 2, h - 2, 10),
            smooth=True, splinesteps=12, fill=PANEL, outline=BORDER, width=1,
        )

        # 标题与右侧胶囊状态标签
        self.create_text(14, 15, text=self._title, anchor="nw", fill=MUTED, font=FONT_SM)

        badge_fg = self._color if self._color != DIM else MUTED
        text_w = self._badge_font.measure(self._badge_text)
        pill_w = max(54, text_w + 27)
        px2, px1 = w - 14, w - 14 - pill_w
        py1, py2 = 12, 33
        self.create_polygon(
            _rounded_rect_points(px1, py1, px2, py2, 9),
            smooth=True, splinesteps=10, fill=self._badge_bg, outline="",
        )
        self.create_oval(px1 + 8, py1 + 7, px1 + 14, py1 + 13, fill=self._color, outline="")
        self.create_text(
            px1 + 19, (py1 + py2) / 2, text=self._badge_text, anchor="w",
            fill=badge_fg, font=FONT_TINY,
        )

        # 副标题
        self.create_text(
            14, 40, text=self._subtitle, anchor="nw", fill=DIM,
            font=FONT_TINY, width=max(80, w - 28),
        )

        # 主状态值
        val_fg = TEXT if self._color == GREEN else self._color
        self.create_text(
            14, 68, text=self._status_text, anchor="nw", fill=val_fg, font=FONT_CARD_VAL,
        )

        # 底部详情
        if self._detail_text:
            self.create_text(
                14, 101, text=self._detail_text, anchor="nw",
                fill=MUTED if self._color != DIM else DIM,
                font=FONT_TINY, width=max(80, w - 28),
            )


class LauncherApp:
    def __init__(self) -> None:
        self.cfg = store.load()
        self.report: core.Report | None = None
        self.busy = False
        self._polling = False
        self.popen = None
        # Tk 只能在主线程操作：工作线程统一把消息投进队列，由主线程消费
        self._q: queue.Queue = queue.Queue()

        self.root = tk.Tk()
        self.root.title(f"Antigravity Launcher {__version__}")
        self.root.configure(bg=BG)
        self.root.geometry("980x880")
        self.root.minsize(920, 780)
        self.root.option_add("*Font", FONT)
        self._apply_window_icon()
        self.var_preflight = tk.BooleanVar(value=bool(self.cfg.get("preflight_check", True)))

        self._build()

    def _apply_window_icon(self) -> None:
        bundle = getattr(sys, "_MEIPASS", None)
        base = Path(bundle) / "assets" if bundle else Path(__file__).resolve().parent / "assets"
        ico = base / "icon.ico"
        png = base / "icon.png"
        try:
            if sys.platform.startswith("win") and ico.is_file():
                self.root.iconbitmap(default=str(ico))
            elif png.is_file():
                img = tk.PhotoImage(file=str(png))
                self.root.iconphoto(True, img)
                self._icon_ref = img
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=20, pady=16)
        outer.columnconfigure(0, weight=1)
        self.frame = outer

        # 标题行（扁平布局，减少子窗口数量）
        head = tk.Frame(outer, bg=BG)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)

        tk.Label(
            head, text="✦  Antigravity Launcher", bg=BG, fg=TEXT, font=FONT_TITLE, anchor="w"
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            head, text="进程级代理注入 · 不开 TUN · 不改系统代理",
            bg=BG, fg=MUTED, font=FONT_SM, anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        actions = tk.Frame(head, bg=BG)
        actions.grid(row=0, column=1, rowspan=2, sticky="e")
        self.btn_client = self._button(actions, "指定客户端", self._pick_client, width=10)
        self.btn_client.pack(side="left", padx=3)
        self.btn_diag = self._button(actions, "网络诊断", self._diagnose, width=9)
        self.btn_diag.pack(side="left", padx=3)
        self.btn_cmds = self._button(actions, "复制命令", self._show_commands, width=9)
        self.btn_cmds.pack(side="left", padx=3)
        self.btn_rescan = self._button(actions, "重新检测", self._detect, width=10, primary=True)
        self.btn_rescan.pack(side="left", padx=(3, 0))

        # 客户端路径条（单 Label 直接带边框，无外层容器）
        self.lbl_client = tk.Label(
            outer, text="正在定位客户端…", bg=PANEL_2, fg=MUTED, font=FONT_TINY,
            anchor="w", padx=12, pady=6, highlightbackground=BORDER, highlightthickness=1,
        )
        self.lbl_client.grid(row=1, column=0, sticky="ew", pady=(10, 10))

        # 三条通道（3 个单 HWND 矢量 Card）
        cards = tk.Frame(outer, bg=BG)
        cards.grid(row=2, column=0, sticky="ew")
        for i in range(3):
            cards.columnconfigure(i, weight=1, uniform="cards")
        self.card_proxy = Card(cards, "代理端点", "本机监听 · 双协议\nHTTP / SOCKS5 握手")
        self.card_proxy.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.card_login = Card(cards, "登录端点", "accounts.google.com\nGoogle OAuth")
        self.card_login.grid(row=0, column=1, sticky="ew", padx=4)
        self.card_model = Card(cards, "模型端点", "daily-cloudcode-pa.googleapis.com")
        self.card_model.grid(row=0, column=2, sticky="ew", padx=(6, 0))

        # 代理端点输入
        row = tk.Frame(outer, bg=BG)
        row.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        row.columnconfigure(1, weight=1)
        tk.Label(row, text="代理端点", bg=BG, fg=MUTED, font=FONT_SM).grid(
            row=0, column=0, padx=(2, 10)
        )
        self.entry_proxy = tk.Entry(
            row, bg=PANEL, fg=TEXT, insertbackground=ACCENT, relief="flat",
            highlightbackground=BORDER, highlightcolor=BORDER_FOCUS, highlightthickness=1,
            font=FONT_MONO, selectbackground=ACCENT_SOFT, selectforeground=TEXT,
        )
        self.entry_proxy.grid(row=0, column=1, sticky="ew", ipady=5)
        self.entry_proxy.bind("<Return>", lambda _e: self._apply_manual())
        if self.cfg.get("proxy_url"):
            self.entry_proxy.insert(0, str(self.cfg["proxy_url"]))
        self.btn_apply = self._button(row, "应用", self._apply_manual, width=7)
        self.btn_apply.grid(row=0, column=2, padx=(8, 4))
        self.btn_auto = self._button(row, "自动探测", self._clear_manual, width=9)
        self.btn_auto.grid(row=0, column=3, padx=(0, 0))

        # 残留实例提示条
        self.residual = tk.Frame(
            outer, bg=RED_SOFT, highlightbackground=RED_BORDER, highlightthickness=1
        )
        self.residual.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        self.residual.columnconfigure(0, weight=1)
        self.lbl_residual = tk.Label(
            self.residual, text="", bg=RED_SOFT, fg="#8e2727", font=FONT_SM,
            anchor="w", justify="left", wraplength=700,
        )
        self.lbl_residual.grid(row=0, column=0, sticky="ew", padx=14, pady=9)
        self.btn_kill = self._button(self.residual, "终止全部实例", self._kill_residuals, width=13, danger=True)
        self.btn_kill.grid(row=0, column=1, padx=12, pady=9)
        self.residual.grid_remove()

        # 下区：左环境变量预览 / 右运行日志
        lower = tk.Frame(outer, bg=BG)
        lower.grid(row=5, column=0, sticky="nsew", pady=(12, 0))
        outer.rowconfigure(5, weight=1)
        lower.columnconfigure(0, weight=56, uniform="lower")
        lower.columnconfigure(1, weight=44, uniform="lower")
        lower.rowconfigure(0, weight=1, minsize=180)

        envbox = tk.Frame(lower, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        envbox.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        envbox.grid_propagate(False)
        envbox.columnconfigure(0, weight=1)
        envbox.rowconfigure(1, weight=1)
        tk.Label(
            envbox, text="将写入子进程的环境变量", bg=PANEL, fg=MUTED, font=FONT_SM
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 4))
        self.txt_env = tk.Text(
            envbox, height=5, bg=PANEL, fg=TEXT, relief="flat", font=FONT_MONO,
            insertwidth=0, highlightthickness=0, wrap="none",
            selectbackground=ACCENT_SOFT, selectforeground=TEXT,
        )
        self.txt_env.grid(row=1, column=0, sticky="nsew", padx=14, pady=(2, 10))
        self.txt_env.configure(state="disabled")

        logbox = tk.Frame(lower, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        logbox.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        logbox.grid_propagate(False)
        logbox.columnconfigure(0, weight=1)
        logbox.rowconfigure(1, weight=1)
        tk.Label(logbox, text="运行日志", bg=PANEL, fg=MUTED, font=FONT_SM).grid(
            row=0, column=0, sticky="w", padx=14, pady=(10, 4)
        )
        self.txt_log = tk.Text(
            logbox, height=5, bg=PANEL, fg=MUTED, relief="flat", font=FONT_MONO,
            highlightthickness=0, wrap="word",
            selectbackground=ACCENT_SOFT, selectforeground=TEXT,
        )
        self.txt_log.grid(row=1, column=0, sticky="nsew", padx=14, pady=(2, 10))
        self.txt_log.configure(state="disabled")
        for tag, color in (("ok", GREEN), ("err", RED), ("warn", AMBER), ("dim", DIM)):
            self.txt_log.tag_configure(tag, foreground=color)

        # 底部启动条 + CLI/终端按钮 + 前置 Google 校验开关
        footer = tk.Frame(outer, bg=BG)
        footer.grid(row=6, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)

        self.btn_launch = tk.Button(
            footer, text="正在检测…", command=self._launch, bg=PANEL_2, fg=DIM,
            activebackground=ACCENT_HOVER, activeforeground="#ffffff",
            disabledforeground=DIM,
            highlightthickness=0, relief="flat", bd=0, font=FONT_BTN,
            state="disabled", cursor="hand2",
        )
        self.btn_launch.grid(row=0, column=0, sticky="ew", ipady=10)
        _bind_hover(self.btn_launch, PANEL_2, PANEL_2)

        self.btn_cli = tk.Button(
            footer, text="启动 agy", command=self._launch_cli_or_terminal, bg=PANEL_2, fg=DIM,
            activebackground=PANEL_HOVER, activeforeground=TEXT,
            disabledforeground=DIM,
            highlightthickness=0, relief="flat", bd=0, font=FONT_SM,
            width=13, state="disabled", cursor="hand2",
        )
        self.btn_cli.grid(row=0, column=1, sticky="ns", padx=(8, 0), ipady=10)
        _bind_hover(self.btn_cli, PANEL_2, PANEL_HOVER)

        self.chk_preflight = tk.Checkbutton(
            footer, text="Google 校验", variable=self.var_preflight,
            command=self._toggle_preflight,
            bg=BG, fg=MUTED, selectcolor=PANEL,
            activebackground=BG, activeforeground=TEXT,
            highlightthickness=0, bd=0, font=FONT_SM, cursor="hand2",
        )
        self.chk_preflight.grid(row=0, column=2, sticky="ns", padx=(12, 0))

        self.root.after(120, self._detect)
        self.root.after(100, self._drain)
        self.root.after(3000, self._poll_loop)

    def _button(
        self, master: tk.Misc, text: str, cmd: Callable, width: int = 10,
        primary: bool = False, danger: bool = False,
    ) -> tk.Button:
        if primary:
            bg, hover, fg, dis_fg = ACCENT, ACCENT_HOVER, "#ffffff", "#f6dfd7"
        elif danger:
            bg, hover, fg, dis_fg = RED, RED_HOVER, "#ffffff", "#f5d6d6"
        else:
            bg, hover, fg, dis_fg = PANEL_2, PANEL_HOVER, TEXT, DIM

        btn = tk.Button(
            master, text=text, command=cmd, bg=bg, fg=fg, relief="flat", bd=0,
            highlightthickness=0, disabledforeground=dis_fg,
            font=FONT_SM, width=width, cursor="hand2",
            activebackground=hover, activeforeground=fg,
            padx=8, pady=5,
        )
        _bind_hover(btn, bg, hover)
        return btn

    def _set_launch_button(
        self,
        *,
        text: str,
        state: str,
        bg: str,
        fg: str,
        hover_bg: str | None = None,
    ) -> None:
        self.btn_launch.configure(
            text=text,
            state=state,
            bg=bg,
            fg=fg,
            disabledforeground=fg,
            activebackground=hover_bg or bg,
            activeforeground=fg,
        )
        self.btn_launch.unbind("<Enter>")
        self.btn_launch.unbind("<Leave>")
        _bind_hover(self.btn_launch, bg, hover_bg or bg)

    # -------------------------------------------------------------- helpers
    def _post(self, fn: Callable, *args) -> None:
        """把要在主线程执行的调用塞进队列。工作线程只碰这个。"""
        self._q.put(("call", fn, args))

    def _log(self, message: str, tag: str | None = None) -> None:
        self._q.put(("log", (message, tag)))

    def _drain(self) -> None:
        drained = False
        try:
            while True:
                kind, payload, *rest = self._q.get_nowait()
                drained = True
                if kind == "log":
                    message, tag = payload
                    self._write_log(message, tag)
                else:
                    fn = payload
                    fn(*rest[0] if rest else ())
        except queue.Empty:
            pass
        except Exception as exc:  # noqa: BLE001
            self._write_log(f"界面回调异常：{type(exc).__name__}: {exc}", "err")
        finally:
            self.root.after(40 if (drained or self.busy) else 120, self._drain)

    def _write_log(self, message: str, tag: str | None) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", f"{time.strftime('%H:%M:%S')}  ", "dim")
        self.txt_log.insert("end", message + "\n", tag or "")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _logf(self, message: str) -> None:
        """供工作线程使用。"""
        self._log(message)

    def _set_text(self, widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (
            self.btn_rescan, self.btn_apply, self.btn_auto, self.btn_client,
            self.btn_diag, self.btn_cmds, self.chk_preflight,
        ):
            btn.configure(state=state)
        if busy:
            self._set_launch_button(
                text=label or "检测中…", state="disabled", bg=PANEL_2, fg=DIM
            )
            self.btn_cli.configure(state="disabled", bg=PANEL_2, fg=DIM)
        else:
            self.btn_rescan.configure(bg=ACCENT)
            for btn in (self.btn_apply, self.btn_auto, self.btn_client, self.btn_diag, self.btn_cmds):
                btn.configure(bg=PANEL_2)

    # ------------------------------------------------------------- 探测
    def _detect(self) -> None:
        if self.busy:
            return
        self._set_busy(True, "检测中…")
        self.card_proxy.set_state("检测中", "正在扫描本机代理端口", DIM, badge_text="扫描中")
        self.card_login.set_state("检测中", "等待握手", DIM, badge_text="等待中")
        self.card_model.set_state("检测中", "等待握手", DIM, badge_text="等待中")

        client_path = self.cfg.get("client_path")
        cli_path = self.cfg.get("cli_path")
        manual = (self.entry_proxy.get() or "").strip() or self.cfg.get("proxy_url") or None
        attempts = int(self.cfg.get("verify_attempts", 2))
        timeout = float(self.cfg.get("verify_timeout", 6.0))
        launched_record = self.cfg.get("launched_desktop")

        def work() -> None:
            try:
                report = core.inspect(
                    client_path=client_path,
                    cli_path=cli_path,
                    proxy_url=manual,
                    attempts=attempts,
                    timeout=timeout,
                    launched_record=launched_record,
                    on_log=self._logf,
                )
            except Exception as exc:  # noqa: BLE001
                self._log(f"探测异常：{type(exc).__name__}: {exc}", "err")
                self._post(self._on_detect_failed)
                return
            self._post(self._apply_report, report)

        threading.Thread(target=work, daemon=True).start()

    def _apply_report(self, report: core.Report) -> None:
        self.report = report
        if self.cfg.get("launched_desktop") and report.own_desktop is None:
            self.cfg["launched_desktop"] = None
            store.save(self.cfg)
        self._set_busy(False)

        if report.client:
            root_txt = str(report.client.root)
            if len(root_txt) > 54:
                root_txt = "…" + root_txt[-53:]
            cli_txt = "agy 已就绪" if report.cli else "未检测到 agy"
            self.lbl_client.configure(
                text=f"客户端：{root_txt}   ·   language_server 已确认   ·   {cli_txt}", fg=MUTED
            )
        else:
            self.lbl_client.configure(text="未找到客户端，请点右上角「指定客户端」", fg=AMBER)

        ep = report.endpoint
        if ep is None:
            self.card_proxy.set_state("未发现", "没有端点通过握手", RED, badge_text="未发现")
            self.card_login.set_state("未通过", "无可用代理端点", RED, badge_text="未通过")
            self.card_model.set_state("未通过", "无可用代理端点", RED, badge_text="未通过")
            self._set_text(self.txt_env, "（代理未就绪，暂不生成环境变量）")
        else:
            if ep.proto_http and ep.proto_socks:
                proto_txt = "双协议"
            elif ep.proto_http:
                proto_txt = "仅 CONNECT"
            elif ep.proto_socks:
                proto_txt = "仅 SOCKS5"
            else:
                proto_txt = "无协议通过"
            self.card_proxy.set_state(
                f"{ep.addr}",
                f"{ep.origin or '来源未知'} · {proto_txt} · {ep.elapsed:.1f}s",
                GREEN if ep.usable else RED,
                badge_text="已连接" if ep.usable else "不可用",
            )
            for card, ch in ((self.card_login, ep.login), (self.card_model, ep.model)):
                if ch.ok:
                    card.set_state("通过", ch.detail, GREEN, badge_text="已通过")
                else:
                    card.set_state("未通过", ch.detail, RED, badge_text="未通过")

            if report.plan:
                lines = [format_preview(k, v) for k, v in report.plan.preview]
                self._set_text(self.txt_env, "\n".join(lines))
            else:
                self._set_text(self.txt_env, "（代理未就绪，暂不生成环境变量）")

        self._refresh_runtime_ui()

    def _refresh_runtime_ui(self) -> None:
        report = self.report
        if report is None:
            return

        # 残留实例（只显示外部未受控实例）
        if report.residuals:
            counts: dict[str, int] = {}
            for p in report.residuals:
                counts[p.name] = counts.get(p.name, 0) + 1
            summary = "   ".join(f"{n} ×{c}" for n, c in sorted(counts.items(), key=lambda kv: -kv[1]))
            self.lbl_residual.configure(
                text=(
                    f"残留实例 {len(report.residuals)} 个：旧进程树收不到代理，必须退出后才能被接管。\n"
                    f"{summary}"
                )
            )
            self.residual.grid()
        else:
            self.residual.grid_remove()

        # 主启动条状态
        if report.own_desktop and not report.residuals and report.endpoint and report.endpoint.usable:
            self._set_launch_button(
                text=f"已启动 · 代理已注入本进程树 (PID {report.own_desktop.pid})",
                state="disabled",
                bg=GREEN_SOFT,
                fg=GREEN,
            )
        elif report.blocked:
            self._set_launch_button(
                text=f"已阻断：{report.block_reason}",
                state="disabled",
                bg=AMBER_SOFT,
                fg=AMBER,
            )
        elif report.degraded:
            self._set_launch_button(
                text="仍要启动（有通道未通过）",
                state="normal",
                bg=AMBER,
                fg="#ffffff",
                hover_bg="#9b5c07",
            )
        else:
            self._set_launch_button(
                text="启动 Antigravity（注入代理）",
                state="normal",
                bg=ACCENT,
                fg="#ffffff",
                hover_bg=ACCENT_HOVER,
            )

        # CLI / 代理终端按钮
        usable = bool(report.endpoint and report.endpoint.usable)
        cli_label = "启动 agy" if report.cli else "代理终端"
        if report.cli_sessions:
            cli_label += f" ({len(report.cli_sessions)})"
        self.btn_cli.configure(
            text=cli_label,
            state="normal" if usable else "disabled",
            bg=PANEL_2,
            fg=TEXT if usable else DIM,
        )
        self._fit_height()

    def _poll_loop(self) -> None:
        """后台定期刷新进程状态，识别己方客户端退出或外部新开残留实例。"""
        try:
            if not self.busy and self.report is not None and not self._polling:
                self._polling = True
                launched_record = self.cfg.get("launched_desktop")

                def work() -> None:
                    try:
                        snap = procs.snapshot()
                        own, residuals = procs.split_running(snap, launched_record)
                        cli_sessions = procs.find_cli_sessions(snap)
                        self._post(self._on_poll_result, own, residuals, cli_sessions)
                    except Exception:  # noqa: BLE001
                        self._post(self._clear_polling_flag)

                threading.Thread(target=work, daemon=True).start()
        finally:
            self.root.after(3000, self._poll_loop)

    def _clear_polling_flag(self) -> None:
        self._polling = False

    def _on_poll_result(
        self,
        own: procs.ProcInfo | None,
        residuals: list[procs.ProcInfo],
        cli_sessions: list[procs.ProcInfo],
    ) -> None:
        self._polling = False
        if self.report is None or self.busy:
            return
        old_own = self.report.own_desktop.pid if self.report.own_desktop else None
        new_own = own.pid if own else None
        old_res = [p.pid for p in self.report.residuals]
        new_res = [p.pid for p in residuals]
        changed = (
            old_own != new_own
            or old_res != new_res
            or len(self.report.cli_sessions) != len(cli_sessions)
        )
        if not changed:
            return

        if old_own is not None and new_own is None:
            self.cfg["launched_desktop"] = None
            store.save(self.cfg)
            self._log("Antigravity 已退出，恢复就绪状态", "dim")
        elif not old_res and new_res:
            self._log(f"检测到新启动的外部残留实例（{len(new_res)} 个）", "warn")

        self.report.own_desktop = own
        self.report.residuals = residuals
        self.report.cli_sessions = cli_sessions
        self._refresh_runtime_ui()

    def _fit_height(self) -> None:
        """内容变高（例如冒出残留实例面板）时把窗口撑到放得下，上限为屏幕高度。"""
        self.root.update_idletasks()
        need = self.frame.winfo_reqheight() + 30
        cur = self.root.winfo_height()
        if need <= cur + 4:
            return
        limit = self.root.winfo_screenheight() - 90
        target = int(min(need, limit))
        if target > cur:
            self.root.geometry(f"{self.root.winfo_width()}x{target}")

    # ------------------------------------------------------------- 操作
    def _toggle_preflight(self) -> None:
        on = bool(self.var_preflight.get())
        self.cfg["preflight_check"] = on
        store.save(self.cfg)
        self._log("已开启启动前 Google 校验" if on else "已关闭启动前 Google 校验", "dim")

    def _pick_client(self) -> None:
        filetypes = (
            [("可执行文件", "*.exe"), ("所有文件", "*.*")]
            if sys.platform.startswith("win")
            else [("所有文件", "*.*")]
        )
        path = filedialog.askopenfilename(
            title="选择 Antigravity 可执行文件",
            filetypes=filetypes,
        )
        if not path:
            return
        from .client import language_server_for

        exe = Path(path)
        if not language_server_for(exe):
            messagebox.showwarning(
                "不是目标客户端",
                "这个文件旁边没有 resources/bin/language_server，\n"
                "看起来不是 Antigravity 客户端的主程序。",
            )
            return
        self.cfg["client_path"] = str(exe)
        store.save(self.cfg)
        self._log(f"已记住客户端路径：{exe}", "ok")
        self._detect()

    def _apply_manual(self) -> None:
        raw = (self.entry_proxy.get() or "").strip()
        if not raw:
            self._clear_manual()
            return
        self.cfg["proxy_url"] = raw
        store.save(self.cfg)
        self._log(f"手动指定代理：{raw}", "ok")
        self._detect()

    def _clear_manual(self) -> None:
        self.cfg["proxy_url"] = None
        store.save(self.cfg)
        self.entry_proxy.delete(0, "end")
        self._log("已切回自动探测")
        self._detect()

    def _kill_residuals(self) -> None:
        if self.busy:
            return
        _own, residuals = procs.split_running(record=self.cfg.get("launched_desktop"))
        if not residuals:
            self._detect()
            return
        names = "、".join(sorted({p.name for p in residuals}))
        if not messagebox.askyesno(
            "终止残留实例",
            f"将关闭 {len(residuals)} 个进程（{names}）。\n\n"
            "未保存的编辑内容会丢失。确定继续？",
        ):
            return

        self._set_busy(True, "正在关闭旧实例…")
        self._log(
            f"正在关闭 {len(residuals)} 个实例：先发送退出请求，最多等 6 秒，未退出再强制结束…",
            "dim",
        )

        def work() -> None:
            try:
                closed, still = procs.close_instances(residuals)
            except Exception as exc:  # noqa: BLE001
                self._post(
                    self._on_residuals_closed,
                    [],
                    [p.pid for p in residuals],
                    f"{type(exc).__name__}: {exc}",
                )
                return
            self._post(self._on_residuals_closed, closed, still)

        threading.Thread(target=work, daemon=True).start()

    def _on_residuals_closed(
        self, closed: list[int], still: list[int], error: str = ""
    ) -> None:
        if error:
            self._log(f"关闭实例失败：{error}", "err")
        elif still:
            self._log(f"仍有 {len(still)} 个进程未退出：{still}", "err")
        else:
            self._log(f"已关闭 {len(closed)} 个实例，环境干净", "ok")

        # 必须先解除 busy：_detect() 在 busy 为 True 时会直接 return，
        # 漏掉这一步界面会永远停在「正在关闭旧实例…」。
        self._set_busy(False)
        self._detect()

    def _launch(self) -> None:
        if self.busy or self.report is None:
            return
        report = self.report
        if report.client is None or report.endpoint is None or not report.endpoint.usable:
            self._log("前置条件不满足，拒绝启动", "err")
            return

        # 点击这一刻再查一次，防止检测之后又冒出实例
        own, residuals = procs.split_running(record=self.cfg.get("launched_desktop"))
        if own and not residuals:
            report.own_desktop = own
            self._refresh_runtime_ui()
            return
        if residuals:
            self._log("启动前复查发现残留实例，已拦下", "err")
            report.residuals = residuals
            self._refresh_runtime_ui()
            return

        if report.degraded:
            if not messagebox.askyesno(
                "通道未全绿",
                "登录端点与模型端点至少有一条未通过握手。\n"
                "现在启动，客户端很可能仍无法登录或无法拉取模型。\n\n继续？",
            ):
                return

        if self.var_preflight.get():
            self._set_busy(True, "正在校验 Google 可达性…")
            timeout = float(self.cfg.get("verify_timeout", 6.0))

            def preflight_work() -> None:
                pf = proxy.check_google_reachability(
                    report.endpoint,
                    user=report.proxy_user,
                    pwd=report.proxy_pwd,
                    attempts=2,
                    timeout=timeout,
                    on_attempt=lambda idx, ok, detail: self._log(
                        f"Google 校验第 {idx} 次：{'通过 ' + detail if ok else detail}",
                        "ok" if ok else "warn",
                    ),
                )
                self._post(self._on_preflight_done, report, pf)

            threading.Thread(target=preflight_work, daemon=True).start()
            return

        self._log("已跳过 Google 真实可达性校验", "dim")
        self._spawn_and_verify(report)

    def _on_preflight_done(self, report: core.Report, pf: proxy.PreflightResult) -> None:
        if not pf.ok:
            self._set_busy(False)
            self._refresh_runtime_ui()
            self._log(f"Google 可达性校验失败，已取消启动：{pf.detail}", "err")
            messagebox.showwarning(
                "Google 不可达",
                f"本地代理握手成功，但无法通过该代理访问 Google（www.google.com/generate_204）：\n\n"
                f"{pf.detail}\n\n"
                "请检查代理节点是否有效；如需强制跳过，可取消勾选右下角「Google 校验」。",
            )
            return
        self._log(f"Google 校验通过：{pf.detail}（{pf.ms}ms）", "ok")
        self._spawn_and_verify(report)

    def _spawn_and_verify(self, report: core.Report) -> None:
        try:
            plan = build_plan(report.endpoint, user=report.proxy_user, pwd=report.proxy_pwd)
            launched_at = time.time()
            popen = spawn(report.client, plan)
            self.popen = popen
        except Exception as exc:  # noqa: BLE001
            self._set_busy(False)
            self._refresh_runtime_ui()
            self._log(f"拉起失败：{type(exc).__name__}: {exc}", "err")
            messagebox.showerror("拉起失败", f"{type(exc).__name__}: {exc}")
            return

        self._log(f"已拉起进程 PID {popen.pid}", "ok")
        self._log(describe_argv(report.client, plan), "dim")
        self._set_busy(True, "等待 language_server 接管…")

        def work() -> None:
            ok, message = verify_injection(popen.pid, launched_at, timeout=90, popen=popen)
            created = launched_at
            if ok:
                snap = procs.snapshot()
                proc_rec = next((p for p in snap if p.pid == popen.pid), None)
                if proc_rec and proc_rec.created > 0:
                    created = proc_rec.created
            tag = "ok" if ok else "warn"
            self._log(message, tag)
            self._post(self._on_launched, ok, message, popen.pid, created, str(report.client.exe))

        threading.Thread(target=work, daemon=True).start()

    def _launch_cli_or_terminal(self) -> None:
        if self.busy or self.report is None:
            return
        report = self.report
        if report.endpoint is None or not report.endpoint.usable:
            self._log("代理未就绪，无法打开代理终端", "err")
            return

        def open_now() -> None:
            wd = open_proxy_terminal(
                report.endpoint,
                user=report.proxy_user,
                pwd=report.proxy_pwd,
                work_dir=self.cfg.get("work_dir"),
                run_exe=report.cli.exe if report.cli else None,
            )
            if report.cli:
                self._log(f"已在 {wd} 打开终端并启动 agy（{report.cli.exe}）", "ok")
            else:
                self._log(f"未检测到 agy，已在 {wd} 打开注入代理的终端", "ok")

        if report.cli and self.var_preflight.get():
            self._set_busy(True, "正在校验 Google 可达性…")
            timeout = float(self.cfg.get("verify_timeout", 6.0))

            def work() -> None:
                pf = proxy.check_google_reachability(
                    report.endpoint,
                    user=report.proxy_user,
                    pwd=report.proxy_pwd,
                    attempts=2,
                    timeout=timeout,
                )
                self._post(self._on_cli_preflight_done, pf, open_now)

            threading.Thread(target=work, daemon=True).start()
            return

        open_now()

    def _on_cli_preflight_done(self, pf: proxy.PreflightResult, open_now: Callable[[], None]) -> None:
        self._set_busy(False)
        self._refresh_runtime_ui()
        if not pf.ok:
            self._log(f"Google 可达性校验失败，取消启动 agy：{pf.detail}", "err")
            messagebox.showwarning("Google 不可达", f"无法通过当前代理访问 Google：\n\n{pf.detail}")
            return
        self._log(f"Google 校验通过：{pf.detail}（{pf.ms}ms）", "ok")
        open_now()

    def _diagnose(self) -> None:
        if self.busy:
            return
        report = self.report
        ep = report.endpoint if report else None
        user = report.proxy_user if report else None
        pwd = report.proxy_pwd if report else None
        timeout = float(self.cfg.get("verify_timeout", 6.0))
        self._set_busy(True, "正在诊断网络…")
        self._log("开始网络诊断（出口 IP 对比 + 核心端点延迟）…", "dim")

        def work() -> None:
            res = proxy.diagnose_network(ep, user=user, pwd=pwd, timeout=timeout)
            self._post(self._on_diag_done, res)

        threading.Thread(target=work, daemon=True).start()

    def _on_diag_done(self, res: proxy.NetDiagResult) -> None:
        self._set_busy(False)
        self._refresh_runtime_ui()
        if res.direct_ip:
            loc = " ".join(x for x in (res.direct_ip.country, res.direct_ip.city) if x)
            self._log(f"直连出口 IP：{res.direct_ip.ip}（{loc}）", "dim")
        else:
            self._log(f"直连出口 IP 查询失败：{res.direct_error}", "warn")

        if res.proxy_ip:
            loc = " ".join(x for x in (res.proxy_ip.country, res.proxy_ip.city) if x)
            self._log(f"代理出口 IP：{res.proxy_ip.ip}（{loc}）", "ok")
        else:
            self._log(f"代理出口 IP 查询失败：{res.proxy_error}", "warn")

        for item in res.pings:
            tag = "ok" if item.ok else "err"
            self._log(f"测速 {item.label} ({item.host})：{item.ms}ms · {item.detail}", tag)

    def _show_commands(self) -> None:
        if self.report is None or self.report.endpoint is None or not self.report.endpoint.usable:
            messagebox.showinfo("暂无可用代理", "请先等待代理探测通过，再生成终端代理命令。")
            return
        cmds = build_proxy_commands(
            self.report.endpoint,
            user=self.report.proxy_user,
            pwd=self.report.proxy_pwd,
        )
        win = tk.Toplevel(self.root)
        win.title("复制终端代理命令")
        win.configure(bg=BG)
        win.geometry("760x490")
        win.transient(self.root)

        outer = tk.Frame(win, bg=BG)
        outer.pack(fill="both", expand=True, padx=20, pady=16)
        outer.columnconfigure(0, weight=1)

        items = [
            ("PowerShell", cmds["powershell"]),
            ("Windows CMD", cmds["cmd"]),
            ("Bash / Zsh (macOS / Linux / Git Bash)", cmds["bash"]),
            ("Git 全局代理", cmds["git"]),
            ("清除环境变量 (PowerShell)", cmds["unset_powershell"]),
        ]
        for idx, (label, val) in enumerate(items):
            tk.Label(outer, text=label, bg=BG, fg=MUTED, font=FONT_SM).grid(
                row=idx * 2, column=0, sticky="w", pady=(8 if idx else 0, 3)
            )
            box = tk.Frame(outer, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
            box.grid(row=idx * 2 + 1, column=0, sticky="ew")
            box.columnconfigure(0, weight=1)
            entry = tk.Entry(
                box, bg=PANEL, fg=TEXT, readonlybackground=PANEL, relief="flat",
                font=FONT_MONO, highlightthickness=0,
                selectbackground=ACCENT_SOFT, selectforeground=TEXT,
            )
            entry.insert(0, val)
            entry.configure(state="readonly")
            entry.grid(row=0, column=0, sticky="ew", padx=10, pady=7)

            def copy_text(txt: str = val, title: str = label) -> None:
                self.root.clipboard_clear()
                self.root.clipboard_append(txt)
                self._log(f"已复制 {title} 代理命令到剪贴板", "ok")

            self._button(box, "复制", copy_text, width=6).grid(row=0, column=1, padx=8, pady=5)

    def _on_detect_failed(self) -> None:
        self._set_busy(False)
        self._set_launch_button(
            text="探测失败，点重新检测",
            state="disabled",
            bg=RED_SOFT,
            fg=RED,
        )

    def _on_launched(self, ok: bool, message: str, pid: int, created: float, exe: str) -> None:
        self._set_busy(False)
        if ok:
            self.cfg["launched_desktop"] = {"pid": pid, "created": created, "exe": exe}
            store.save(self.cfg)
            if self.report:
                snap = procs.snapshot()
                own, residuals = procs.split_running(snap, self.cfg["launched_desktop"])
                self.report.own_desktop = own
                self.report.residuals = residuals
        self._after_launch(ok, message, pid)

    def _after_launch(self, ok: bool, message: str, pid: int = 0) -> None:
        self._refresh_runtime_ui()
        if ok:
            self._set_launch_button(
                text=f"已启动 · 代理已注入本进程树 (PID {pid})",
                state="disabled",
                bg=GREEN_SOFT,
                fg=GREEN,
            )
            self.lbl_client.configure(
                text="代理只作用于这一棵进程树；关闭客户端即失效", fg=MUTED
            )
        else:
            self._set_launch_button(
                text="已拉起，但未确认接管 → 点重新检测",
                state="normal",
                bg=ACCENT,
                fg="#ffffff",
                hover_bg=ACCENT_HOVER,
            )

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    LauncherApp().run()
