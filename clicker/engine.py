"""连点、录制和宏的执行引擎；所有工作均不占用 Tk 主线程。"""
from __future__ import annotations

import threading
import time
from typing import Callable

from .win32 import InputSender, normalize_hotkey

FinishCallback = Callable[[bool, str], None]

# Event.wait 的超时会被系统计时器向上取整到 15.625ms 的整数倍，0.01 秒的间隔就变成
# 0.0156 秒。改用 sleep 分片轮询停止标志，短间隔才是真的；1ms 一片也保证急停不拖沓。
_SLEEP_SLICE = 0.001


class TaskRunner:
    """一个可中断的后台任务，wake() 可立即打断当前等待。"""

    def __init__(self, sender: InputSender | None = None):
        self.sender = sender or InputSender()
        self._wake = threading.Event()
        self._stop_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False

    @property
    def running(self) -> bool:
        with self._stop_lock:
            return self._running

    def _start(self, target, *args) -> None:
        if self.running:
            raise RuntimeError("任务已经在运行")
        self._wake.clear()
        with self._stop_lock:
            self._running = True
        self._thread = threading.Thread(target=target, args=args, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._stop_lock:
            self._running = False
            self._wake.set()
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(2)

    def sleep(self, seconds: float) -> bool:
        deadline = time.perf_counter() + max(0.0, seconds)
        while True:
            if self._wake.is_set():
                return True
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return False
            time.sleep(min(remaining, _SLEEP_SLICE))

    def _finish(self) -> None:
        with self._stop_lock:
            self._running = False


class Clicker(TaskRunner):
    def start(self, settings: dict, on_finish: FinishCallback) -> bool:
        if self.running:
            self.stop()
            return False
        try:
            kind = settings["kind"]
            interval = float(settings["interval"])
            count = int(settings["count"])
            if not 0.001 <= interval <= 3600:
                raise ValueError("间隔须在 0.001–3600 秒之间")
            if not 0 <= count <= 10000000:
                raise ValueError("次数无效")
            vk = normalize_hotkey(settings["key"])[0] if kind == "key" else 0
        except (KeyError, ValueError) as exc:
            raise ValueError(f"连点配置无效：{exc}")

        def work():
            ok, message = True, "连点已停止"
            try:
                for index in range(count) if count else iter(int, 1):
                    if not self.running:
                        break
                    if settings.get("position"):
                        self.sender.move(settings["x"], settings["y"])
                    if kind in ("left", "right", "middle"):
                        self.sender.mouse_button(kind, True)
                        self.sender.mouse_button(kind, False)
                    elif kind == "wheel_up":
                        self.sender.wheel("up")
                    elif kind == "wheel_down":
                        self.sender.wheel("down")
                    else:
                        self.sender.key(vk, True)
                        self.sender.key(vk, False)
                    # 最后一次点击后也保持可停止，但不额外等待。
                    if (not count or index + 1 < count) and self.sleep(interval):
                        break
                if ok:
                    message = "连点完成" if count else "连点已停止"
            except Exception as exc:
                ok, message = False, f"连点出错：{exc}"
            finally:
                self._finish()
                on_finish(ok, message)

        self._start(work)
        return True


class MacroRunner(TaskRunner):
    def __init__(self, store, sender: InputSender | None = None):
        super().__init__(sender)
        self.store = store

    def start(self, macro: dict, on_finish: FinishCallback) -> bool:
        if self.running:
            self.stop()
            return False
        name = macro.get("name", "宏")

        def work():
            ok, message = True, "宏已停止"
            try:
                repeat = int(macro.get("repeat", 1))
                completed = 0
                for _ in range(repeat) if repeat else iter(int, 1):
                    for step in macro["steps"]:
                        if not self.running:
                            raise InterruptedError
                        self._execute(step)
                    completed += 1
                message = f"宏「{name}」完成，共执行 {completed} 次"
            except InterruptedError:
                pass
            except Exception as exc:
                ok, message = False, f"宏执行出错：{exc}"
            finally:
                self._finish()
                on_finish(ok, message)

        self._start(work)
        return True

    def _execute(self, step: dict) -> None:
        kind = step["type"]
        if kind == "wait":
            if self.sleep(float(step["seconds"])):
                raise InterruptedError
        elif kind == "recording":
            events = self.store.load("recording", step["name"])["events"]
            for event in events:
                if not self.running or self.sleep(event["dt"]):
                    raise InterruptedError
                self._event(event)
        elif kind == "click":
            self.sender.mouse_button(step["button"], True)
            self.sender.mouse_button(step["button"], False)
        elif kind == "move":
            self.sender.move(step["x"], step["y"])
        elif kind == "wheel":
            self.sender.wheel(step["direction"])
        elif kind == "key":
            vk = normalize_hotkey(step["key"])[0]
            self.sender.key(vk, True)
            self.sender.key(vk, False)
        elif kind == "hotkey":
            # 依次按下修饰键与主键，再逆序释放，支持 Ctrl+C 等组合。
            parts = [p.strip() for p in step["key"].split("+")]
            codes = [normalize_hotkey(p)[0] for p in parts]
            for vk in codes:
                self.sender.key(vk, True)
            for vk in reversed(codes):
                self.sender.key(vk, False)
        elif kind == "text":
            # 直接发送 Unicode（包括中文），不读写或覆盖用户剪贴板。
            self.sender.text(step["text"])
            if self.sleep(0.02):
                raise InterruptedError
        else:
            raise ValueError(f"未知宏步骤：{kind}")

    def _event(self, event: dict) -> None:
        kind = event["type"]
        if kind == "mouse_down":
            self.sender.mouse_button(event["button"], True)
        elif kind == "mouse_up":
            self.sender.mouse_button(event["button"], False)
        elif kind == "wheel":
            for _ in range(max(1, abs(event["delta"]) // 120)):
                self.sender.wheel("up" if event["delta"] > 0 else "down")
        elif kind == "key_down":
            self.sender.key(event["vk"], True)
        elif kind == "key_up":
            self.sender.key(event["vk"], False)
        elif kind == "move":
            self.sender.move(event["x"], event["y"])


class MouseRecorder:
    """鼠标钩子、定时坐标采样和全局键盘钩子共同记录操作。"""

    def __init__(self, on_event):
        self.on_event = on_event
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.ready = threading.Event()
        self.error: str | None = None
        self._hook = None
        self._callback = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.ready.clear()
        self.error = None
        self.thread = threading.Thread(target=self._run, name="鼠标录制钩子", daemon=True)
        self.thread.start()
        self.ready.wait(5)
        if self.error:
            raise RuntimeError(self.error)

    def stop(self):
        if self.thread_id:
            from .win32 import user32
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(2)
        self.thread = None
        self.thread_id = 0

    def _run(self):
        import ctypes
        from ctypes import wintypes
        from .win32 import HOOKPROC, kernel32, user32

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", wintypes.WPARAM),
            ]

        self.thread_id = kernel32.GetCurrentThreadId()

        @HOOKPROC
        def callback(code, wparam, lparam):
            if code >= 0:
                data = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not data.flags & 0x01:  # LLMHF_INJECTED
                    buttons = {
                        0x0201: ("mouse_down", "left"), 0x0202: ("mouse_up", "left"),
                        0x0204: ("mouse_down", "right"), 0x0205: ("mouse_up", "right"),
                        0x0207: ("mouse_down", "middle"), 0x0208: ("mouse_up", "middle"),
                    }
                    try:
                        if wparam in buttons:
                            kind, button = buttons[wparam]
                            self.on_event(kind, button=button)
                        elif wparam == 0x020A:
                            raw = (data.mouseData >> 16) & 0xFFFF
                            delta = ctypes.c_short(raw).value
                            self.on_event("wheel", delta=delta)
                    except Exception:
                        pass
            return user32.CallNextHookEx(self._hook, code, wparam, lparam)

        self._callback = callback
        self._hook = user32.SetWindowsHookExW(14, callback, kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            self.error = f"无法安装鼠标录制钩子：{ctypes.WinError(ctypes.get_last_error())}"
            self.ready.set()
            return
        self.ready.set()
        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                pass
        finally:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None


class RecordingSession:
    def __init__(self, on_finish: FinishCallback, ignore_vk=()):
        self.events: list[dict] = []
        self._ignore_vk = {int(vk) for vk in ignore_vk}
        self._lock = threading.Lock()
        self._running = False
        self._started = 0.0
        self._last_time = 0.0
        self._last_move: tuple[int, int] | None = None
        self._keyboard = None
        self._mouse = None
        self._poll_thread = None
        self._on_finish = on_finish

    @property
    def running(self):
        return self._running

    def count(self) -> int:
        with self._lock:
            return len(self.events)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(event) for event in self.events]

    def start(self):
        if self._running:
            return
        self.events = []
        self._last_move = None
        self._running = True
        try:
            self._keyboard = __import__("clicker.win32", fromlist=["KeyboardHook"]).KeyboardHook(self.key_event)
            self._keyboard.start()
            self._mouse = MouseRecorder(self.mouse_event)
            self._mouse.start()
            self._started = self._last_time = time.perf_counter()
            self._poll_thread = threading.Thread(target=self._poll_mouse, name="鼠标轨迹采样", daemon=True)
            self._poll_thread.start()
        except Exception:
            self.stop()
            raise

    def key_event(self, vk: int, scan: int, down: bool):
        if vk in self._ignore_vk:
            # 启停录制用的热键本身不该出现在录制结果里。
            return
        self._append({"type": "key_down" if down else "key_up", "vk": vk})

    def mouse_event(self, kind: str, **kwargs):
        self._append({"type": kind, **kwargs})

    def _append(self, event: dict):
        if not self._running:
            return
        now = time.perf_counter()
        with self._lock:
            event["dt"] = max(0.0, now - self._last_time)
            self._last_time = now
            self.events.append(event)

    def _poll_mouse(self):
        sender = InputSender()
        while self._running:
            try:
                position = sender.cursor_position()
                if position != self._last_move:
                    if self._last_move is not None:
                        self._append({"type": "move", "x": position[0], "y": position[1]})
                    self._last_move = position
            except Exception:
                pass
            time.sleep(0.02)

    def stop(self):
        if not self._running:
            return
        self._running = False
        if self._keyboard:
            self._keyboard.stop()
        if self._mouse:
            self._mouse.stop()
        if self._poll_thread and self._poll_thread is not threading.current_thread():
            self._poll_thread.join(1)
        # 播放时坐标绝对可复现，初始位置单独记录，不增加操作延迟。
        events = list(self.events)
        if events:
            first_move = next((e for e in events if e["type"] == "move"), None)
            if first_move:
                first_move["dt"] = 0.0
        self._on_finish(True, f"录制完成：{len(events)} 个事件，时长 {time.perf_counter() - self._started:.2f} 秒")
