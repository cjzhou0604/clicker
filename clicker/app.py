"""连点器主界面：连点、录制、宏三个功能页，以及全局快捷键的注册与调度。"""
from __future__ import annotations

import math
import queue
import time
import tkinter as tk
from tkinter import messagebox, ttk

from .engine import Clicker, MacroRunner, MouseRecorder, RecordingSession
from .store import Store
from .win32 import (
    HotkeyListener, InputSender, VK_NAMES, apply_dpi_awareness, key_name,
    normalize_hotkey, pressed_modifiers,
)

CLICK_KINDS = (
    ("鼠标左键", "left"), ("鼠标右键", "right"), ("鼠标中键", "middle"),
    ("滚轮向上", "wheel_up"), ("滚轮向下", "wheel_down"), ("键盘按键", "key"),
)
BUTTON_CN = {"left": "左键", "right": "右键", "middle": "中键"}
CLICK_KIND_CN = {value: text for text, value in CLICK_KINDS}
STEP_TYPES = (
    ("调用已录制操作", "recording"), ("鼠标点击", "click"), ("移动鼠标", "move"),
    ("滚轮滚动", "wheel"), ("按下按键", "key"), ("组合键", "hotkey"),
    ("输入文本", "text"), ("等待", "wait"),
)
STEP_KIND_BY_LABEL = {text: value for text, value in STEP_TYPES}
MODIFIER_KEYSYMS = {
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
    "Win_L", "Win_R", "Menu", "Caps_Lock", "Num_Lock", "Scroll_Lock",
}
KEY_CHOICES = sorted(VK_NAMES, key=str.casefold)
EVENT_CN = {
    "mouse_down": "鼠标按下", "mouse_up": "鼠标松开", "wheel": "滚轮",
    "key_down": "按键按下", "key_up": "按键松开", "move": "移动",
}
PICK_LABEL = "取光标位置"
PICK_TIMEOUT = 20.0


def format_seconds(value: float) -> str:
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text or "0"


def hotkey_from_event(event, modifiers=None) -> str | None:
    """把一次按键事件转换为 ``Ctrl+Shift+F8`` 形式的快捷键。"""
    if event.keysym in MODIFIER_KEYSYMS or event.keysym == "Escape":
        return None
    if modifiers is None:
        modifiers = pressed_modifiers()
    return "+".join(list(modifiers) + [event.keysym.upper()])


def step_label(step: dict) -> str:
    kind = step["type"]
    if kind == "recording":
        return f"调用录制「{step['name']}」"
    if kind == "click":
        return f"鼠标{BUTTON_CN[step['button']]}点击"
    if kind == "move":
        return f"移动到 ({step['x']}, {step['y']})"
    if kind == "wheel":
        return f"滚轮{'向上' if step['direction'] == 'up' else '向下'}"
    if kind == "key":
        return f"按下 {step['key']}"
    if kind == "hotkey":
        return f"组合键 {step['key']}"
    if kind == "text":
        shown = step["text"].replace("\n", "\\n").replace("\t", "\\t")
        return f"输入文本 “{shown[:28]}{'…' if len(shown) > 28 else ''}”"
    if kind == "wait":
        return f"等待 {format_seconds(step['seconds'])} 秒"
    return kind


def event_label(event: dict) -> tuple[str, str]:
    kind = event["type"]
    if kind in ("mouse_down", "mouse_up"):
        return EVENT_CN[kind], BUTTON_CN[event["button"]]
    if kind == "wheel":
        step_count = abs(event["delta"]) // 120 or 1
        return "滚轮", f"{'向上' if event['delta'] > 0 else '向下'} ×{step_count}"
    if kind in ("key_down", "key_up"):
        return EVENT_CN[kind], key_name(event["vk"])
    return "移动", f"({event['x']}, {event['y']})"


class UiBridge:
    """后台线程只往队列里投递回调，所有 Tk 操作仍在主线程执行。"""

    def __init__(self, root: tk.Misc):
        self._queue: queue.Queue = queue.Queue()
        self._root = root
        self._closed = False
        self._pump()

    def submit(self, action, *args) -> None:
        if not self._closed:
            self._queue.put((action, args))

    def close(self) -> None:
        self._closed = True

    def _pump(self) -> None:
        try:
            while True:
                action, args = self._queue.get_nowait()
                try:
                    action(*args)
                except Exception as exc:  # 界面上的异常不应终止程序
                    print(f"界面回调异常：{exc}")
        except queue.Empty:
            pass
        if not self._closed:
            self._root.after(40, self._pump)


class App(tk.Tk):
    def __init__(self, store: Store | None = None):
        super().__init__()
        self.title("连点器")
        # 高分屏下 Tk 会按实际 DPI 放大控件，窗口尺寸改为按屏幕比例计算。
        width = max(900, min(int(self.winfo_screenwidth() * 0.6), 1500))
        height = max(650, min(int(self.winfo_screenheight() * 0.78), 1050))
        self.geometry(f"{width}x{height}")
        self.minsize(int(width * 0.7), int(height * 0.7))

        self.store = store or Store()
        self.config = self.store.load_config()
        self.sender = InputSender()
        self.clicker = Clicker(sender=self.sender)
        self.macro_runner = MacroRunner(self.store, sender=self.sender)
        self.recording = RecordingSession(self._recording_finished)
        self.hotkeys = HotkeyListener(self._on_hotkey)
        self.bridge = UiBridge(self)

        self.steps: list[dict] = []
        self.editing_recording = ""
        self.editing_macro = ""
        self.record_state = "空闲"
        self._hotkey_entries: dict[str, str] | None = None
        self._summary_cache = ""
        # 延时播放中的任务：（说明文字, 截止时刻, 启动函数）
        self._pending: tuple[str, float, object] | None = None
        self._minimized_for_playback = False
        self._stop_requested = False
        self._playback_busy = False
        self._playback_locked: list[ttk.Button] = []
        # 取坐标模式：按下按钮后等用户在别处真实点一下鼠标，那一点才是目标坐标
        self._pick_hook: MouseRecorder | None = None
        self._pick_button: ttk.Button | None = None
        self._pick_targets: tuple[tk.StringVar, tk.StringVar] | None = None
        self._pick_deadline = 0.0

        self._setup_style()
        self._build_vars()
        self._build_ui()
        self._load_config_into_ui()
        self.refresh_lists()
        self.refresh_hotkeys()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(150, self._poll)

    def _load_config_into_ui(self) -> None:
        click = self.config["click"]
        self.primary_hotkey.set(self.config.get("hotkey", "F8"))
        self.stop_hotkey.set(self.config.get("stop_hotkey", "F9"))
        self.playback_hotkey.set(self.config.get("playback_hotkey", "F7"))
        self.click_kind.set(click.get("kind", "left"))
        self.click_key.set(click.get("key", "A"))
        self.click_count.set(str(click.get("count", 0)))
        self.click_position.set(bool(click.get("position", False)))
        self.click_x.set(str(click.get("x", 0)))
        self.click_y.set(str(click.get("y", 0)))
        interval = click.get("interval", 0.1)
        preset = f"{interval:g}" if f"{interval:g}" in ("0.1", "0.01") else "custom"
        if preset == "custom":
            self.interval_custom.set(f"{interval:g}")
        self._select_preset(preset)
        playback = self.config.get("playback", {})
        self.playback_minimize.set(bool(playback.get("minimize", False)))
        self.playback_delay.set(f"{float(playback.get('delay', 0)):g}")
        self.key_combo.configure(state="readonly" if self.click_kind.get() == "key" else "disabled")
        self._on_position_toggle()

    # ------------------------------------------------------------------ 通用
    def _setup_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Section.TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Run.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Status.TLabel", padding=(6, 2))
        style.configure("Running.TLabel", foreground="#0a7d28")
        style.configure("Idle.TLabel", foreground="#444444")

    def _build_vars(self) -> None:
        self.click_kind = tk.StringVar()
        self.interval_mode = tk.StringVar(value="preset")
        self.interval_preset = tk.StringVar(value="0.1")
        self.interval_custom = tk.StringVar(value="0.05")
        self.click_count = tk.StringVar(value="0")
        self.click_key = tk.StringVar(value="A")
        self.click_position = tk.BooleanVar(value=False)
        self.click_x = tk.StringVar(value="0")
        self.click_y = tk.StringVar(value="0")
        self.primary_hotkey = tk.StringVar()
        self.stop_hotkey = tk.StringVar()
        self.playback_hotkey = tk.StringVar()
        self.rec_name = tk.StringVar()
        self.rec_hotkey = tk.StringVar()
        self.macro_name = tk.StringVar()
        self.macro_repeat = tk.StringVar(value="1")
        self.macro_hotkey = tk.StringVar()
        self.step_type = tk.StringVar(value=STEP_TYPES[0][0])
        self.step_recording = tk.StringVar()
        self.step_button = tk.StringVar(value="left")
        self.step_direction = tk.StringVar(value="up")
        self.step_key = tk.StringVar(value="A")
        self.step_hotkey = tk.StringVar(value="Ctrl+C")
        self.step_text = tk.StringVar()
        self.step_seconds = tk.StringVar(value="0.2")
        self.step_x = tk.StringVar(value="0")
        self.step_y = tk.StringVar(value="0")
        self.playback_minimize = tk.BooleanVar(value=False)
        self.playback_delay = tk.StringVar(value="0")
        self.status = tk.StringVar(value="就绪")

    def _build_ui(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        self.click_tab = ttk.Frame(self.notebook, padding=12)
        self.record_tab = ttk.Frame(self.notebook, padding=12)
        self.macro_tab = ttk.Frame(self.notebook, padding=12)
        self.options_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.click_tab, text="  连点  ")
        self.notebook.add(self.record_tab, text="  录制  ")
        self.notebook.add(self.macro_tab, text="  宏  ")
        self.notebook.add(self.options_tab, text="  选项  ")
        self._build_click_tab()
        self._build_record_tab()
        self._build_macro_tab()
        self._build_options_tab()

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(bar, text="数据目录：").pack(side="left")
        ttk.Label(bar, text=str(self.store.data_dir), style="Status.TLabel").pack(side="left")
        self.status_label = ttk.Label(bar, textvariable=self.status, anchor="w")
        self.status_label.pack(side="right", fill="x", expand=True, padx=8)

    def _bind_capture(self, widget: ttk.Entry, var: tk.StringVar, on_change) -> None:
        """在快捷键输入框里直接按下组合键即可完成配置。"""
        def handler(event):
            value = hotkey_from_event(event)
            if value is None:
                return None
            var.set(value)
            on_change()
            return "break"
        widget.bind("<KeyPress>", handler, add="+")
        widget.bind("<FocusOut>", lambda _e: on_change(), add="+")

    def _fail(self, message: str) -> None:
        self.status.set(f"⚠ {message}")
        self.bell()

    # ------------------------------------------------------------------ 连点
    def _build_click_tab(self) -> None:
        left = ttk.Frame(self.click_tab)
        left.pack(side="left", fill="y", padx=(0, 12))
        right = ttk.Frame(self.click_tab)
        right.pack(side="left", fill="both", expand=True)

        kinds = ttk.LabelFrame(left, text="点击类型", padding=10, style="Section.TLabelframe")
        kinds.pack(fill="x", pady=(0, 10))
        for text, value in CLICK_KINDS:
            ttk.Radiobutton(
                kinds, text=text, value=value, variable=self.click_kind,
                command=self._on_click_kind_change,
            ).pack(anchor="w", pady=1)
            if value == "key":
                self.key_combo = ttk.Combobox(
                    kinds, textvariable=self.click_key, values=KEY_CHOICES, width=14)
                self.key_combo.pack(fill="x", pady=(2, 1))

        timing = ttk.LabelFrame(left, text="每次点击间隔", padding=10, style="Section.TLabelframe")
        timing.pack(fill="x", pady=(0, 10))
        preset_row = ttk.Frame(timing)
        preset_row.pack(anchor="w")
        for value in ("0.1", "0.01"):
            ttk.Radiobutton(
                preset_row, text=f"{value} 秒", value=value,
                variable=self.interval_preset, command=lambda v=value: self._select_preset(v),
            ).pack(side="left", padx=(0, 10))
        custom_row = ttk.Frame(timing)
        custom_row.pack(anchor="w", pady=(6, 0))
        self.custom_radio = ttk.Radiobutton(
            custom_row, text="自定义", value="custom", variable=self.interval_preset,
            command=lambda: self._select_preset("custom"))
        self.custom_radio.pack(side="left")
        self.custom_entry = ttk.Entry(custom_row, textvariable=self.interval_custom, width=10)
        self.custom_entry.pack(side="left", padx=(6, 4))
        ttk.Label(custom_row, text="秒").pack(side="left")

        options = ttk.LabelFrame(left, text="其他", padding=10, style="Section.TLabelframe")
        options.pack(fill="x", pady=(0, 10))
        row = ttk.Frame(options)
        row.pack(anchor="w", fill="x")
        ttk.Label(row, text="点击次数").pack(side="left")
        ttk.Entry(row, textvariable=self.click_count, width=10).pack(side="left", padx=6)
        ttk.Label(row, text="0 = 不限").pack(side="left")
        ttk.Checkbutton(
            options, text="固定在指定坐标点击", variable=self.click_position,
            command=self._on_position_toggle,
        ).pack(anchor="w", pady=(6, 2))
        pos_row = ttk.Frame(options)
        pos_row.pack(anchor="w", fill="x")
        self.x_entry = ttk.Entry(pos_row, textvariable=self.click_x, width=8)
        self.x_entry.pack(side="left")
        ttk.Label(pos_row, text=" ,  ").pack(side="left")
        self.y_entry = ttk.Entry(pos_row, textvariable=self.click_y, width=8)
        self.y_entry.pack(side="left", padx=(0, 8))
        self.pick_button = ttk.Button(pos_row, text=PICK_LABEL, command=self._pick_position)
        self.pick_button.pack(side="left")

        self.click_toggle = ttk.Button(
            right, text="启动连点（F8）", style="Run.TButton", command=self.toggle_click)
        self.click_toggle.pack(fill="x", pady=(0, 10))
        self.click_status = ttk.Label(right, text="状态：就绪", style="Idle.TLabel")
        self.click_status.pack(anchor="w")

        preview = ttk.LabelFrame(right, text="当前设置", padding=10, style="Section.TLabelframe")
        preview.pack(fill="both", expand=True, pady=(10, 0))
        self.click_summary = tk.Text(preview, height=8, wrap="word", relief="flat",
                                     font=("Microsoft YaHei UI", 10))
        self.click_summary.pack(fill="both", expand=True)
        self.click_summary.configure(state="disabled")
        tips = (
            "提示：间隔 0.01 秒对多数程序已接近极限，若目标窗口无响应请调大间隔。\n"
            "以管理员身份运行的程序需要本程序同样以管理员身份运行才能接收到点击。"
        )
        ttk.Label(right, text=tips, justify="left", foreground="#555555").pack(anchor="w", pady=(10, 0))

    def interval_seconds(self) -> float:
        raw = self.interval_custom.get() if self.interval_preset.get() == "custom" else self.interval_preset.get()
        try:
            return float(raw)
        except ValueError:
            raise ValueError(f"无效的间隔：{raw!r}")

    def _select_preset(self, value: str) -> None:
        self.interval_preset.set(value)
        state = "normal" if value == "custom" else "disabled"
        self.custom_entry.configure(state=state)
        self._update_click_summary()

    def _on_click_kind_change(self) -> None:
        enabled = self.click_kind.get() == "key"
        self.key_combo.configure(state="readonly" if enabled else "disabled")
        self._update_click_summary()

    def _on_position_toggle(self) -> None:
        state = "normal" if self.click_position.get() else "disabled"
        for widget in (self.x_entry, self.y_entry, self.pick_button):
            widget.configure(state=state)
        self._update_click_summary()

    def _pick_position(self) -> None:
        self._start_pick(self.pick_button, (self.click_x, self.click_y))

    def _start_pick(self, button: ttk.Button, targets: tuple[tk.StringVar, tk.StringVar]) -> None:
        """进入取坐标模式；下一次真实点击的位置写回 targets。"""
        if self._pick_button is button:
            self._end_pick("已取消取坐标")
            return
        if self.recording.running:
            self._fail("录制进行中不能取坐标，请先结束录制")
            return
        hook = MouseRecorder(self._on_pick_click)
        try:
            hook.start()
        except Exception as exc:
            self._fail(f"无法监听鼠标取坐标：{exc}")
            return
        self._end_pick()
        self._pick_hook = hook
        self._pick_button = button
        self._pick_targets = targets
        self._pick_deadline = time.monotonic() + PICK_TIMEOUT
        button.configure(text="请点选位置…")
        self.status.set(
            f"移动到新位置点一下鼠标即可取坐标，点本窗口无效，{PICK_TIMEOUT:g} 秒内不点则取消")

    def _on_pick_click(self, kind: str, **_kwargs) -> None:
        # 回调运行在钩子线程里，只取坐标后投递回主线程。
        if kind == "mouse_down":
            self.bridge.submit(self._finish_pick, self.sender.cursor_position())

    def _finish_pick(self, position: tuple[int, int]) -> None:
        if self._pick_targets is None:
            return
        x, y = position
        left, top, right, bottom = self.sender.window_rect(self.winfo_id())
        if left <= x < right and top <= y < bottom:
            # 点在自己窗口上不改变目标，继续等待。
            return
        self._pick_targets[0].set(str(x))
        self._pick_targets[1].set(str(y))
        self._end_pick()
        self.status.set(f"已取坐标：({x}, {y})")
        self._update_click_summary()

    def _end_pick(self, message: str | None = None) -> None:
        if self._pick_button is not None:
            self._pick_button.configure(text=PICK_LABEL)
        hook, self._pick_hook = self._pick_hook, None
        if hook is not None:
            hook.stop()
        self._pick_button = None
        self._pick_targets = None
        if message:
            self.status.set(message)

    def click_settings(self) -> dict:
        return {
            "kind": self.click_kind.get(),
            "interval": self.interval_seconds(),
            "count": int(self.click_count.get() or 0),
            "key": self.click_key.get(),
            "position": bool(self.click_position.get()),
            "x": int(self.click_x.get() or 0),
            "y": int(self.click_y.get() or 0),
        }

    def _update_click_summary(self) -> None:
        try:
            settings = self.click_settings()
            name = CLICK_KIND_CN.get(settings["kind"], settings["kind"])
            lines = [
                f"点击类型：{name}" + (f"（{settings['key']}）" if settings["kind"] == "key" else ""),
                f"点击间隔：{format_seconds(settings['interval'])} 秒",
                f"点击次数：{'不限' if not settings['count'] else settings['count']}",
                f"点击位置：{'固定 (%s, %s)' % (settings['x'], settings['y']) if settings['position'] else '跟随光标'}",
                f"启停热键：{self.primary_hotkey.get() or '未设置'}",
            ]
        except (KeyError, ValueError, tk.TclError) as exc:
            lines = [f"设置不完整：{exc}"]
        text = "\n".join(lines)
        if text == self._summary_cache:
            return
        self._summary_cache = text
        self.click_summary.configure(state="normal")
        self.click_summary.delete("1.0", "end")
        self.click_summary.insert("1.0", text)
        self.click_summary.configure(state="disabled")

    def toggle_click(self) -> None:
        if self.clicker.running:
            self.clicker.stop()
            self.status.set("连点已停止")
            return
        try:
            settings = self.click_settings()
        except ValueError as exc:
            self._fail(str(exc))
            return
        try:
            self.clicker.start(settings, lambda ok, msg: self.bridge.submit(self._task_finished, "click", ok, msg))
        except ValueError as exc:
            self._fail(str(exc))
            return
        self.save_config()
        self.status.set("连点已启动")

    # ------------------------------------------------------------------ 录制
    def _build_record_tab(self) -> None:
        controls = ttk.Frame(self.record_tab)
        controls.pack(fill="x", pady=(0, 10))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="名称").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.rec_name, width=20).grid(
            row=0, column=1, sticky="w", padx=(6, 12))
        ttk.Label(controls, text="调用快捷键").grid(row=0, column=2, sticky="w")
        entry = ttk.Entry(controls, textvariable=self.rec_hotkey, width=14)
        entry.grid(row=0, column=3, sticky="w", padx=(6, 12))
        self._bind_capture(entry, self.rec_hotkey, self._pending_hotkey_hint)
        self.record_status = ttk.Label(controls, text="空闲", style="Idle.TLabel")
        self.record_status.grid(row=0, column=4, sticky="w")
        buttons = ttk.Frame(controls)
        buttons.grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))
        self.record_toggle = ttk.Button(buttons, text="开始录制（F8）", style="Run.TButton",
                                        command=self.toggle_record)
        self.rec_save = ttk.Button(buttons, text="保存", command=self.save_recording)
        self.rec_delete = ttk.Button(buttons, text="删除", command=self.delete_recording)
        self.rec_play = ttk.Button(buttons, text="播放选中", command=self.play_selected)
        self.rec_stop = ttk.Button(buttons, text="停止播放", command=self.stop_playback)
        for button in (self.record_toggle, self.rec_save, self.rec_delete,
                       self.rec_play, self.rec_stop):
            button.pack(side="left", padx=6)
        self._playback_locked.extend((self.record_toggle, self.rec_save,
                                      self.rec_delete, self.rec_play))

        body = ttk.Frame(self.record_tab)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=3)
        body.rowconfigure(0, weight=1)
        saved = ttk.LabelFrame(body, text="已保存的录制", padding=6, style="Section.TLabelframe")
        saved.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.rec_list = ttk.Treeview(
            saved, columns=("name", "events", "seconds", "hotkey"), show="headings", height=16)
        for column, text, width in (
            ("name", "名称", 160), ("events", "事件", 60),
            ("seconds", "时长", 70), ("hotkey", "快捷键", 100),
        ):
            self.rec_list.heading(column, text=text)
            self.rec_list.column(column, width=width, minwidth=45,
                                 anchor="w" if column == "name" else "center")
        self.rec_list.pack(side="left", fill="both", expand=True)
        self.rec_list.bind("<<TreeviewSelect>>", lambda _e: self._load_recording_selection())
        scrollbar = ttk.Scrollbar(saved, orient="vertical", command=self.rec_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.rec_list.configure(yscrollcommand=scrollbar.set)

        preview = ttk.LabelFrame(body, text="事件明细", padding=6, style="Section.TLabelframe")
        preview.grid(row=0, column=1, sticky="nsew")
        self.event_view = ttk.Treeview(
            preview, columns=("index", "type", "detail", "delay"), show="headings", height=16)
        for column, text, width in (
            ("index", "#", 50), ("type", "类型", 100),
            ("detail", "内容", 200), ("delay", "延迟(秒)", 80),
        ):
            self.event_view.heading(column, text=text)
            self.event_view.column(column, width=width, minwidth=45,
                                   anchor="w" if column == "detail" else "center")
        self.event_view.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(preview, orient="vertical", command=self.event_view.yview)
        scroll.pack(side="right", fill="y")
        self.event_view.configure(yscrollcommand=scroll.set)
        ttk.Label(
            self.record_tab,
            text="录制内容包含鼠标点击、滚轮、轨迹与全部键盘按键；播放时按录制的时间间隔重现。",
            foreground="#555555").pack(anchor="w", pady=(8, 0))

    def toggle_record(self) -> None:
        if self.recording.running:
            self.recording.stop()
            return
        if self.rec_name.get().strip():
            self.editing_recording = self.rec_name.get().strip()
        self.recording = RecordingSession(
            self._recording_finished, ignore_vk=self._reserved_hotkey_vks())
        try:
            self.recording.start()
        except Exception as exc:
            self.recording = RecordingSession(self._recording_finished)
            self._fail(f"无法开始录制：{exc}")
            return
        self.record_state = "录制中"
        self.status.set("开始录制，按启停热键结束")

    def _recording_finished(self, ok: bool, message: str) -> None:
        self.bridge.submit(self._show_recording, ok, message)

    def _show_recording(self, ok: bool, message: str) -> None:
        self.record_state = "空闲"
        self.status.set(message if ok else f"⚠ {message}")
        events = self.recording.snapshot()
        if events and not self.rec_name.get().strip():
            self.rec_name.set(f"录制 {time.strftime('%m%d-%H%M%S')}")
        self._fill_event_view(events)
        if events:
            self.status.set(f"录制完成：{len(events)} 个事件，填写名称后点击“保存”")

    def _fill_event_view(self, events: list[dict]) -> None:
        self.event_view.delete(*self.event_view.get_children())
        for index, event in enumerate(events[:2000], start=1):
            kind, detail = event_label(event)
            self.event_view.insert("", "end", values=(
                index, kind, detail, format_seconds(event["dt"])))

    def _load_recording_selection(self) -> None:
        selected = self.rec_list.selection()
        if not selected:
            return
        name = selected[0]
        try:
            document = self.store.load("recording", name)
        except ValueError as exc:
            self._fail(str(exc))
            return
        self.editing_recording = name
        self.rec_name.set(name)
        self.rec_hotkey.set(document.get("hotkey", ""))
        self._fill_event_view(document["events"])

    def save_recording(self) -> None:
        events = self.recording.snapshot()
        if not events:
            selected = self.rec_list.selection()
            if not selected:
                self._fail("没有可保存的录制内容，请先录制")
                return
            try:
                events = self.store.load("recording", selected[0])["events"]
            except ValueError as exc:
                self._fail(str(exc))
                return
        name = self.rec_name.get().strip()
        if not name:
            self._fail("请先填写录制名称")
            return
        try:
            self.store.save("recording", name, {"events": events, "hotkey": self.rec_hotkey.get().strip()})
        except ValueError as exc:
            self._fail(f"保存失败：{exc}")
            return
        if self.editing_recording and self.editing_recording != name:
            self.store.delete("recording", self.editing_recording)
        self.editing_recording = name
        self.config["last_recording"] = name
        self.save_config()
        self.refresh_lists()
        self.refresh_hotkeys()
        self.status.set(f"已保存录制「{name}」：{len(events)} 个事件")

    def delete_recording(self) -> None:
        selected = self.rec_list.selection()
        if not selected:
            self._fail("请先选择要删除的录制")
            return
        name = selected[0]
        if not messagebox.askyesno("删除录制", f"确定删除录制「{name}」吗？", parent=self):
            return
        self.store.delete("recording", name)
        if self.config.get("last_recording") == name:
            self.config["last_recording"] = ""
        if self.editing_recording == name:
            self.editing_recording = ""
            self.rec_name.set("")
            self.rec_hotkey.set("")
        self.save_config()
        self.refresh_lists()
        self.refresh_hotkeys()
        self.status.set(f"已删除录制「{name}」")

    def play_selected(self) -> None:
        selected = self.rec_list.selection()
        if not selected:
            self._fail("请先选择一段录制")
            return
        self.play_recording(selected[0])

    def play_recording(self, name: str) -> None:
        if self.macro_runner.running or self._pending is not None:
            self.stop_playback()
            return
        try:
            self.store.load("recording", name)
        except ValueError as exc:
            self._fail(str(exc))
            return

        def start() -> None:
            self.macro_runner.start(
                {"name": name, "repeat": 1, "steps": [{"type": "recording", "name": name}]},
                lambda ok, msg: self.bridge.submit(self._playback_finished, name, ok, msg))
            self.status.set(f"正在播放录制「{name}」")

        self._begin_playback(f"播放录制「{name}」", start)

    def _playback_finished(self, name: str, ok: bool, message: str) -> None:
        self._restore_after_playback()
        if not ok:
            self._fail(message)
            return
        stopped, self._stop_requested = self._stop_requested, False
        self.status.set(f"已停止播放：录制「{name}」" if stopped else f"播放完成：录制「{name}」")

    def play_last_recording(self) -> None:
        name = self.config.get("last_recording") or ""
        if not name:
            names = self.store.list_names("recording")
            name = names[-1] if names else ""
        if not name:
            self._fail("还没有保存任何录制")
            return
        self.play_recording(name)

    # --------------------------------------------------------------------- 宏
    def _build_macro_tab(self) -> None:
        top = ttk.Frame(self.macro_tab)
        top.pack(fill="x", pady=(0, 8))
        fields = ttk.Frame(top)
        fields.pack(anchor="w")
        ttk.Label(fields, text="宏名称").pack(side="left")
        ttk.Entry(fields, textvariable=self.macro_name, width=18).pack(side="left", padx=(4, 12))
        ttk.Label(fields, text="循环次数").pack(side="left")
        ttk.Entry(fields, textvariable=self.macro_repeat, width=8).pack(side="left", padx=(4, 4))
        ttk.Label(fields, text="0 = 不限").pack(side="left", padx=(0, 12))
        ttk.Label(fields, text="调用快捷键").pack(side="left")
        entry = ttk.Entry(fields, textvariable=self.macro_hotkey, width=14)
        entry.pack(side="left", padx=(4, 4))
        self._bind_capture(entry, self.macro_hotkey, self._pending_hotkey_hint)
        buttons = ttk.Frame(top)
        buttons.pack(anchor="w", pady=(8, 0))
        self.macro_toggle = ttk.Button(buttons, text="运行选中宏", style="Run.TButton",
                                       command=self.toggle_selected_macro)
        self.macro_stop = ttk.Button(buttons, text="停止", command=self.stop_playback)
        self.macro_toggle.pack(side="left", padx=(0, 6))
        self.macro_stop.pack(side="left", padx=6)

        body = ttk.Frame(self.macro_tab)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=5)
        body.rowconfigure(0, weight=1)
        library = ttk.LabelFrame(body, text="宏库", padding=6, style="Section.TLabelframe")
        library.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.macro_list = ttk.Treeview(library, show="tree", height=10)
        self.macro_list.column("#0", width=170, anchor="w")
        self.macro_list.pack(fill="both", expand=True)
        self.macro_list.bind("<<TreeviewSelect>>", lambda _e: self._load_macro_selection())
        row = ttk.Frame(library)
        row.pack(fill="x", pady=(6, 0))
        self.macro_new = ttk.Button(row, text="新建", command=self.new_macro)
        self.macro_save = ttk.Button(row, text="保存", command=self.save_macro)
        self.macro_delete = ttk.Button(row, text="删除", command=self.delete_macro)
        for index, button in enumerate((self.macro_new, self.macro_save, self.macro_delete)):
            button.pack(side="left", padx=(6, 0) if index else 0)
        self._playback_locked.extend((self.macro_new, self.macro_save, self.macro_delete))

        editor = ttk.Frame(body)
        editor.grid(row=0, column=1, sticky="nsew")
        steps = ttk.LabelFrame(editor, text="步骤", padding=6, style="Section.TLabelframe")
        steps.pack(fill="both", expand=True)
        self.step_view = ttk.Treeview(steps, columns=("index", "detail"), show="headings", height=9)
        self.step_view.heading("index", text="#")
        self.step_view.heading("detail", text="操作")
        self.step_view.column("index", width=46, anchor="center")
        self.step_view.column("detail", width=380, anchor="w")
        self.step_view.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(steps, orient="vertical", command=self.step_view.yview)
        scroll.pack(side="right", fill="y")
        self.step_view.configure(yscrollcommand=scroll.set)
        buttons = ttk.Frame(editor)
        buttons.pack(fill="x", pady=(6, 0))
        self.step_remove = ttk.Button(buttons, text="删除选中步骤", command=self.remove_step)
        self.step_up = ttk.Button(buttons, text="上移", command=lambda: self.move_step(-1))
        self.step_down = ttk.Button(buttons, text="下移", command=lambda: self.move_step(1))
        self.step_remove.pack(side="left")
        self.step_up.pack(side="left", padx=6)
        self.step_down.pack(side="left")
        self._playback_locked.extend((self.step_remove, self.step_up, self.step_down))

        form = ttk.LabelFrame(editor, text="添加步骤", padding=8, style="Section.TLabelframe")
        form.pack(fill="x", pady=(10, 0))
        head = ttk.Frame(form)
        head.pack(fill="x")
        self.step_combo = ttk.Combobox(
            head, textvariable=self.step_type, values=[text for text, _ in STEP_TYPES],
            state="readonly", width=16)
        self.step_combo.pack(side="left")
        self.step_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_step_form())
        self.step_add = ttk.Button(head, text="添加", style="Run.TButton", command=self.add_step)
        self.step_add.pack(side="right")
        self._playback_locked.append(self.step_add)
        self.step_fields = ttk.Frame(form)
        self.step_fields.pack(fill="x", pady=(8, 0))

        self.rec_field = ttk.Frame(self.step_fields)
        ttk.Label(self.rec_field, text="录制").pack(side="left")
        self.step_rec_combo = ttk.Combobox(
            self.rec_field, textvariable=self.step_recording, state="readonly", width=26)
        self.step_rec_combo.pack(side="left", padx=6)
        ttk.Button(self.rec_field, text="刷新", command=self.refresh_lists).pack(side="left")

        self.click_field = ttk.Frame(self.step_fields)
        ttk.Label(self.click_field, text="按键").pack(side="left")
        ttk.Combobox(self.click_field, textvariable=self.step_button, state="readonly",
                     values=[f"{cn} {key}" for key, cn in BUTTON_CN.items()], width=12).pack(side="left", padx=6)
        ttk.Label(self.click_field, text="（单击）").pack(side="left")

        self.move_field = ttk.Frame(self.step_fields)
        ttk.Label(self.move_field, text="坐标").pack(side="left")
        ttk.Entry(self.move_field, textvariable=self.step_x, width=8).pack(side="left", padx=(6, 2))
        ttk.Label(self.move_field, text=", ").pack(side="left")
        ttk.Entry(self.move_field, textvariable=self.step_y, width=8).pack(side="left", padx=(2, 6))
        self.step_pick_button = ttk.Button(
            self.move_field, text=PICK_LABEL, command=self._pick_step_position)
        self.step_pick_button.pack(side="left")

        self.wheel_field = ttk.Frame(self.step_fields)
        ttk.Label(self.wheel_field, text="方向").pack(side="left")
        ttk.Combobox(self.wheel_field, textvariable=self.step_direction, state="readonly",
                     values=["up", "down"], width=8).pack(side="left", padx=6)

        self.key_field = ttk.Frame(self.step_fields)
        ttk.Label(self.key_field, text="按键").pack(side="left")
        ttk.Combobox(self.key_field, textvariable=self.step_key, values=KEY_CHOICES,
                     width=14).pack(side="left", padx=6)

        self.hotkey_field = ttk.Frame(self.step_fields)
        ttk.Label(self.hotkey_field, text="组合键").pack(side="left")
        ttk.Entry(self.hotkey_field, textvariable=self.step_hotkey, width=18).pack(side="left", padx=6)
        ttk.Label(self.hotkey_field, text="例如 Ctrl+Shift+S").pack(side="left")

        self.text_field = ttk.Frame(self.step_fields)
        ttk.Label(self.text_field, text="文本").pack(side="left")
        ttk.Entry(self.text_field, textvariable=self.step_text, width=40).pack(side="left", padx=6)

        self.wait_field = ttk.Frame(self.step_fields)
        ttk.Label(self.wait_field, text="等待").pack(side="left")
        ttk.Entry(self.wait_field, textvariable=self.step_seconds, width=10).pack(side="left", padx=6)
        ttk.Label(self.wait_field, text="秒").pack(side="left")

        self.macro_status = ttk.Label(self.macro_tab, text="状态：空闲", style="Idle.TLabel")
        self.macro_status.pack(anchor="w", pady=(8, 0))
        self._update_step_form()

    def _pick_step_position(self) -> None:
        self._start_pick(self.step_pick_button, (self.step_x, self.step_y))

    def _update_step_form(self) -> None:
        current = STEP_KIND_BY_LABEL.get(self.step_type.get(), "recording")
        fields = {
            "recording": self.rec_field, "click": self.click_field, "move": self.move_field,
            "wheel": self.wheel_field, "key": self.key_field, "hotkey": self.hotkey_field,
            "text": self.text_field, "wait": self.wait_field,
        }
        for kind, widget in fields.items():
            if kind == current:
                widget.grid(row=0, column=0, sticky="w")
            else:
                widget.grid_remove()

    def current_step(self) -> dict:
        kind = STEP_KIND_BY_LABEL.get(self.step_type.get(), "recording")
        if kind == "recording":
            name = self.step_recording.get()
            if not name:
                raise ValueError("请选择要调用的录制")
            return {"type": kind, "name": name}
        if kind == "click":
            button = self.step_button.get().split()[-1]
            return {"type": kind, "button": button}
        if kind == "move":
            return {"type": kind, "x": int(self.step_x.get()), "y": int(self.step_y.get())}
        if kind == "wheel":
            return {"type": kind, "direction": self.step_direction.get()}
        if kind == "key":
            return {"type": kind, "key": self.step_key.get().strip()}
        if kind == "hotkey":
            return {"type": kind, "key": self.step_hotkey.get().strip()}
        if kind == "text":
            text = self.step_text.get()
            if not text:
                raise ValueError("请输入要键入的文本")
            return {"type": kind, "text": text}
        return {"type": kind, "seconds": float(self.step_seconds.get())}

    def add_step(self) -> None:
        try:
            step = self.current_step()
        except (ValueError, tk.TclError) as exc:
            self._fail(f"步骤参数无效：{exc}")
            return
        self.steps.append(step)
        self._fill_step_view()
        self.status.set(f"已添加步骤：{step_label(step)}")

    def remove_step(self) -> None:
        selected = self.step_view.selection()
        if not selected:
            self._fail("请先选择要删除的步骤")
            return
        for item in selected:
            self.steps.pop(int(self.step_view.item(item, "values")[0]) - 1)
        self._fill_step_view()

    def move_step(self, offset: int) -> None:
        selected = self.step_view.focus()
        if not selected:
            return
        index = int(self.step_view.item(selected, "values")[0]) - 1
        target = index + offset
        if not 0 <= target < len(self.steps):
            return
        self.steps[index], self.steps[target] = self.steps[target], self.steps[index]
        self._fill_step_view()
        self.step_view.selection_set(str(target))
        self.step_view.focus(str(target))

    def _fill_step_view(self) -> None:
        self.step_view.delete(*self.step_view.get_children())
        for index, step in enumerate(self.steps, start=1):
            self.step_view.insert("", "end", iid=str(index), values=(index, step_label(step)))

    def new_macro(self) -> None:
        self.editing_macro = ""
        self.steps = []
        self.macro_name.set("")
        self.macro_hotkey.set("")
        self.macro_repeat.set("1")
        self._fill_step_view()
        self.macro_list.selection_set("")
        self.status.set("新建宏：填写名称与步骤后保存")

    def save_macro(self) -> None:
        name = self.macro_name.get().strip()
        if not name:
            self._fail("请先填写宏名称")
            return
        if not self.steps:
            self._fail("宏至少要有一个步骤")
            return
        try:
            repeat = int(self.macro_repeat.get() or 1)
            self.store.save("macro", name, {
                "steps": self.steps, "repeat": repeat,
                "hotkey": self.macro_hotkey.get().strip(),
            })
        except ValueError as exc:
            self._fail(f"保存失败：{exc}")
            return
        if self.editing_macro and self.editing_macro != name:
            self.store.delete("macro", self.editing_macro)
        self.editing_macro = name
        self.refresh_lists()
        self.refresh_hotkeys()
        self.status.set(f"已保存宏「{name}」，共 {len(self.steps)} 个步骤")

    def delete_macro(self) -> None:
        selected = self.macro_list.selection()
        if not selected:
            self._fail("请先选择要删除的宏")
            return
        name = selected[0]
        if not messagebox.askyesno("删除宏", f"确定删除宏「{name}」吗？", parent=self):
            return
        self.store.delete("macro", name)
        if self.editing_macro == name:
            self.new_macro()
        self.refresh_lists()
        self.refresh_hotkeys()
        self.status.set(f"已删除宏「{name}」")

    def _load_macro_selection(self) -> None:
        selected = self.macro_list.selection()
        if not selected:
            return
        name = selected[0]
        try:
            document = self.store.load("macro", name)
        except ValueError as exc:
            self._fail(str(exc))
            return
        self.editing_macro = name
        self.macro_name.set(name)
        self.macro_hotkey.set(document.get("hotkey", ""))
        self.macro_repeat.set(str(document.get("repeat", 1)))
        self.steps = [dict(step) for step in document["steps"]]
        self._fill_step_view()

    def toggle_selected_macro(self) -> None:
        if self.macro_runner.running or self._pending is not None:
            self.stop_playback()
            return
        selected = self.macro_list.selection()
        name = self.editing_macro or (selected[0] if selected else "")
        if not name:
            self._fail("请先选择或保存一个宏")
            return
        self.run_macro(name)

    def run_macro(self, name: str) -> None:
        if self.macro_runner.running or self._pending is not None:
            self.stop_playback()
            return
        try:
            document = self.store.load("macro", name)
        except ValueError as exc:
            self._fail(str(exc))
            return
        document["name"] = name

        def start() -> None:
            try:
                self.macro_runner.start(
                    document, lambda ok, msg: self.bridge.submit(self._task_finished, "macro", ok, msg))
            except ValueError as exc:
                self._fail(str(exc))
                return
            self.status.set(f"正在运行宏「{name}」")

        self._begin_playback(f"运行宏「{name}」", start)

    # ------------------------------------------------------------------ 选项
    def _build_options_tab(self) -> None:
        left = ttk.Frame(self.options_tab)
        left.pack(side="left", fill="y", padx=(0, 12))
        right = ttk.Frame(self.options_tab)
        right.pack(side="left", fill="both", expand=True)

        hotkey = ttk.LabelFrame(left, text="全局快捷键", padding=10, style="Section.TLabelframe")
        hotkey.pack(fill="x", pady=(0, 10))
        for text, var in (("启停热键", self.primary_hotkey),
                          ("急停热键", self.stop_hotkey),
                          ("回放上次录制", self.playback_hotkey)):
            row = ttk.Frame(hotkey)
            row.pack(anchor="w", fill="x", pady=2)
            ttk.Label(row, text=text).pack(side="left")
            entry = ttk.Entry(row, textvariable=var, width=14)
            entry.pack(side="left", padx=(8, 0))
            self._bind_capture(entry, var, self._on_hotkey_setting)
        ttk.Label(hotkey, text="在输入框内直接按键即可捕获。\n"
                               "停留在哪一页，启停热键就启停哪一页的功能（选项页除外）；\n"
                               "急停会一次停掉连点、录制和宏的全部后台任务。",
                  justify="left").pack(anchor="w", pady=(8, 0))

        playback = ttk.LabelFrame(left, text="播放录制 / 运行宏", padding=10,
                                  style="Section.TLabelframe")
        playback.pack(fill="x")
        ttk.Checkbutton(
            playback, text="开始前先最小化本窗口，结束后自动还原",
            variable=self.playback_minimize, command=self._on_playback_option,
        ).pack(anchor="w")
        row = ttk.Frame(playback)
        row.pack(anchor="w", fill="x", pady=(6, 0))
        ttk.Label(row, text="延时").pack(side="left")
        self.delay_entry = ttk.Entry(row, textvariable=self.playback_delay, width=8)
        self.delay_entry.pack(side="left", padx=6)
        ttk.Label(row, text="秒后开始（0 = 立即）").pack(side="left")
        for sequence in ("<FocusOut>", "<Return>"):
            self.delay_entry.bind(sequence, lambda _e: self._on_playback_option(), add="+")
        ttk.Label(playback, text="延时期间状态栏会倒计时，按急停可取消；\n"
                                 "这段时间足够把焦点切到目标窗口。",
                  justify="left", foreground="#555555").pack(anchor="w", pady=(8, 0))

        tips = ttk.LabelFrame(right, text="说明", padding=10, style="Section.TLabelframe")
        tips.pack(fill="both", expand=True)
        ttk.Label(tips, justify="left", text=(
            "播放录制和运行宏共用同一个执行通道，\n"
            "同一时刻只有一个在跑。\n"
            "播放期间录制页与宏页的按钮自动置灰，\n"
            "只有「停止播放」「停止运行」可点。\n"
            "勾选最小化后窗口开始时收起，播完还原，\n"
            "状态栏会写明播放完成还是已停止。\n"
            "任何时候按急停热键都能立刻停掉后台任务，\n"
            "包括还没开始的延时播放。"
        )).pack(anchor="w")

    def playback_delay_seconds(self) -> float:
        try:
            value = float(self.playback_delay.get() or 0)
        except ValueError:
            raise ValueError(f"无效的延时：{self.playback_delay.get()!r}")
        if not 0 <= value <= 3600:
            raise ValueError("播放延时须在 0–3600 秒之间")
        return value

    def _on_playback_option(self) -> None:
        try:
            self.playback_delay_seconds()
        except (ValueError, tk.TclError) as exc:
            self._fail(str(exc))
            return
        self.save_config()
        self.status.set("播放选项已保存")

    def _begin_playback(self, label: str, start) -> None:
        """按选项延时或立即启动一个后台播放任务。"""
        try:
            delay = self.playback_delay_seconds()
        except (ValueError, tk.TclError) as exc:
            self._fail(str(exc))
            return
        if delay > 0:
            self._pending = (label, time.monotonic() + delay, start)
            self._tick_pending()
            return
        self._launch(start)

    def _launch(self, start) -> None:
        self._pending = None
        if self.playback_minimize.get():
            self._minimized_for_playback = True
            self.iconify()
        start()

    def _tick_pending(self) -> None:
        if self._pending is None:
            return
        label, deadline, start = self._pending
        remaining = deadline - time.monotonic()
        if remaining > 0:
            self.status.set(f"{math.ceil(remaining)} 秒后{label}，按急停可取消")
            return
        self._launch(start)

    def _cancel_pending(self) -> bool:
        if self._pending is None:
            return False
        self._pending = None
        self.status.set("已取消播放")
        return True

    def _restore_after_playback(self) -> None:
        if not self._minimized_for_playback:
            return
        self._minimized_for_playback = False
        self.deiconify()

    def stop_playback(self) -> None:
        """停掉正在播放的录制或正在运行的宏，或取消等待中的延时播放。"""
        if self._cancel_pending():
            return
        self._stop_requested = True
        self.macro_runner.stop()

    def _apply_playback_lock(self, busy: bool) -> None:
        if busy == self._playback_busy:
            return
        self._playback_busy = busy
        state = "disabled" if busy else "normal"
        for button in self._playback_locked:
            button.configure(state=state)

    # ------------------------------------------------------------- 快捷键与状态
    def _reserved_hotkey_vks(self) -> set[int]:
        vks = set()
        for value in (self.primary_hotkey.get(), self.stop_hotkey.get(),
                      self.playback_hotkey.get()):
            try:
                vks.add(normalize_hotkey(value)[0])
            except ValueError:
                continue
        return vks

    def _on_hotkey_setting(self) -> None:
        self.save_config()
        self.refresh_hotkeys()
        self._update_click_summary()

    def refresh_hotkeys(self) -> None:
        named = [("启停", "primary", self.primary_hotkey.get())]
        named += [("急停", "stop", self.stop_hotkey.get()),
                  ("回放上次录制", "playback", self.playback_hotkey.get())]
        for name in self.store.list_names("recording"):
            try:
                hotkey = self.store.load("recording", name).get("hotkey", "")
            except ValueError:
                continue
            named.append((f"录制「{name}」", f"recording:{name}", hotkey))
        for name in self.store.list_names("macro"):
            try:
                hotkey = self.store.load("macro", name).get("hotkey", "")
            except ValueError:
                continue
            named.append((f"宏「{name}」", f"macro:{name}", hotkey))

        entries: dict[str, str] = {}
        seen: dict[tuple[int, int], str] = {}
        for source, action, value in named:
            if not value or not value.strip():
                continue
            try:
                vk, mods, shown = normalize_hotkey(value)
            except ValueError as exc:
                self._fail(f"{source} 的快捷键无效：{exc}")
                continue
            if (vk, mods) in seen:
                self._fail(f"快捷键 {shown} 同时被“{seen[(vk, mods)]}”和“{source}”使用")
                continue
            seen[(vk, mods)] = source
            entries[action] = value
        if entries == self._hotkey_entries:
            return
        try:
            self.hotkeys.set_hotkeys(entries)
        except Exception as exc:
            self._hotkey_entries = None
            self._fail(str(exc))
            return
        self._hotkey_entries = entries

    def _pending_hotkey_hint(self) -> None:
        self.status.set("快捷键已填写，保存后生效")

    def _on_hotkey(self, action: str) -> None:
        # 回调运行在快捷键线程里，只允许投递到主线程。
        if action == "primary":
            self.bridge.submit(self._primary_action)
        elif action == "stop":
            self.bridge.submit(self.stop_all)
        elif action == "playback":
            self.bridge.submit(self.play_last_recording)
        elif action.startswith("recording:"):
            self.bridge.submit(self.play_recording, action.split(":", 1)[1])
        elif action.startswith("macro:"):
            self.bridge.submit(self.run_macro, action.split(":", 1)[1])

    def _primary_action(self) -> None:
        tab = self.notebook.index(self.notebook.select())
        if tab == 0:
            self.toggle_click()
        elif tab == 1:
            self.toggle_record()
        elif tab == 2:
            self.toggle_selected_macro()
        else:
            self.status.set("选项页只放设置，启停热键请在连点、录制或宏页使用")

    def stop_all(self) -> None:
        self._end_pick()
        if self.recording.running:
            self.recording.stop()
        self.clicker.stop()
        self.stop_playback()
        self.status.set("已停止全部任务")

    def _task_finished(self, which: str, ok: bool, message: str) -> None:
        self._restore_after_playback()
        self._stop_requested = False
        self.status.set(message if ok else f"⚠ {message}")

    def save_config(self) -> None:
        try:
            settings = self.click_settings()
        except (ValueError, tk.TclError):
            settings = self.config["click"]
        self.config.update(
            hotkey=self.primary_hotkey.get().strip(),
            stop_hotkey=self.stop_hotkey.get().strip(),
            playback_hotkey=self.playback_hotkey.get().strip(),
        )
        self.config["click"] = settings
        try:
            delay = self.playback_delay_seconds()
        except (ValueError, tk.TclError):
            delay = float(self.config.get("playback", {}).get("delay", 0))
        self.config["playback"] = {"minimize": bool(self.playback_minimize.get()), "delay": delay}
        try:
            self.store.save_config(self.config)
        except ValueError as exc:
            self._fail(str(exc))

    def refresh_lists(self) -> None:
        self.rec_list.delete(*self.rec_list.get_children())
        for name in self.store.list_names("recording"):
            try:
                document = self.store.load("recording", name)
            except ValueError:
                self.rec_list.insert("", "end", iid=name, text=f"{name}（文件损坏）")
                continue
            events = document["events"]
            total = sum(float(event["dt"]) for event in events)
            self.rec_list.insert("", "end", iid=name, text=name, values=(
                name, len(events), format_seconds(total), document.get("hotkey", "")))
        self.macro_list.delete(*self.macro_list.get_children())
        for name in self.store.list_names("macro"):
            try:
                document = self.store.load("macro", name)
            except ValueError:
                self.macro_list.insert("", "end", iid=name, text=f"{name}（文件损坏）")
                continue
            hotkey = document.get("hotkey", "")
            self.macro_list.insert("", "end", iid=name,
                                   text=f"{name}  [{hotkey}]" if hotkey else name)
        recordings = self.store.list_names("recording")
        self.step_rec_combo.configure(values=recordings)
        if recordings and not self.step_recording.get():
            self.step_recording.set(recordings[0])

    def _poll(self) -> None:
        clicking = self.clicker.running
        self._tick_pending()
        if self._pick_button is not None and time.monotonic() > self._pick_deadline:
            self._end_pick("取坐标已超时取消")
        playing = self.macro_runner.running
        pending = self._pending is not None
        self._apply_playback_lock(playing or pending)
        self.click_toggle.configure(text="停止连点" if clicking else "启动连点")
        self.click_status.configure(
            text=f"状态：{'连点运行中' if clicking else '就绪'}",
            style="Running.TLabel" if clicking else "Idle.TLabel")
        self.record_toggle.configure(
            text="停止录制（F8）" if self.recording.running else "开始录制（F8）")
        self.record_status.configure(
            text=(f"录制中：{self.recording.count()} 个事件" if self.recording.running else self.record_state),
            style="Running.TLabel" if self.recording.running else "Idle.TLabel")
        self.macro_toggle.configure(text="停止运行" if (playing or pending) else "运行选中宏")
        busy_text = "运行中" if playing else ("等待开始" if pending else "空闲")
        self.macro_status.configure(
            text=f"状态：{busy_text}",
            style="Running.TLabel" if (playing or pending) else "Idle.TLabel")
        self._update_click_summary()
        self.after(200, self._poll)

    def on_close(self) -> None:
        self.save_config()
        self._pending = None
        self._end_pick()
        if self.recording.running:
            self.recording.stop()
        self.clicker.stop()
        self.macro_runner.stop()
        self.hotkeys.stop()
        self.bridge.close()
        self.destroy()


def main() -> None:
    apply_dpi_awareness()
    try:
        app = App()
    except OSError as exc:
        from tkinter import messagebox as box
        box.showerror("启动失败", f"连点器无法启动：{exc}")
        raise SystemExit(1) from exc
    app.mainloop()
