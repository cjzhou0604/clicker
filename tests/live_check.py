"""真实输入链路自检（需要桌面会话，手动运行：python tests/live_check.py）。

与 test_core.py 不同，这里会真的调用 SendInput 和系统钩子，但所有输入都落在
本脚本自己创建的空白窗口里，或使用 F13 / Ctrl+Alt+F12 这类不会被别处绑定的按键。
"""
from __future__ import annotations

import ctypes
import sys
import tempfile
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 控制台默认可能是 GBK，状态文本里会出现 ⚠ 等非 GBK 字符。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from clicker.engine import Clicker, MacroRunner, RecordingSession
from clicker.store import Store
from clicker.win32 import (
    HOOKPROC, KBDLLHOOKSTRUCT, WH_KEYBOARD_LL, InputSender, apply_dpi_awareness,
    kernel32, user32,
)

VK_F13 = 0x7C  # 几乎没有程序绑定 F13，适合做注入验证


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", ctypes.c_long * 2), ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class CaptureHook:
    """同时安装键盘与鼠标底层钩子，保留注入事件以便统计回环结果。"""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.ready = threading.Event()
        self._callbacks: list = []
        self._hooks: list = []

    def add(self, kind: str, **data) -> None:
        self.events.append((kind, data))

    def start(self) -> None:
        self.ready.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait(5)

    def stop(self) -> None:
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        if self.thread:
            self.thread.join(2)

    def wait_for(self, predicate, timeout=3.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(list(self.events)):
                return True
        return False

    def _run(self) -> None:
        self.thread_id = kernel32.GetCurrentThreadId()
        hmodule = kernel32.GetModuleHandleW(None)

        @HOOKPROC
        def keyboard(code, wparam, lparam):
            if code >= 0:
                data = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                self.add("key", vk=int(data.vkCode), down=wparam in (0x0100, 0x0104),
                         injected=bool(data.flags & 0x10))
            return user32.CallNextHookEx(None, code, wparam, lparam)

        @HOOKPROC
        def mouse(code, wparam, lparam):
            if code >= 0:
                data = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                self.add("mouse", message=int(wparam), x=int(data.pt[0]), y=int(data.pt[1]),
                         injected=bool(data.flags & 0x01))
            return user32.CallNextHookEx(None, code, wparam, lparam)

        for hook_id, callback in ((WH_KEYBOARD_LL, keyboard), (14, mouse)):
            handle = user32.SetWindowsHookExW(hook_id, callback, hmodule, 0)
            if not handle:
                raise OSError(f"无法安装测试钩子 {hook_id}")
            self._hooks.append(handle)
            self._callbacks.append(callback)
        self.ready.set()
        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                pass
        finally:
            for handle in self._hooks:
                user32.UnhookWindowsHookEx(handle)


def make_target_window() -> tuple[tk.Tk, tuple[int, int]]:
    root = tk.Tk()
    root.title("连点器自检目标")
    root.geometry("360x240+200+200")
    tk.Frame(root, width=360, height=240).pack()
    root.update_idletasks()
    root.update()
    return root, physical_center(root)


def physical_center(root: tk.Tk) -> tuple[int, int]:
    """SetCursorPos 与钩子都用物理像素，Tk 的 winfo 坐标则是逻辑像素，需按 Win32 取。"""
    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    rect = RECT()
    hwnd = wintypes.HWND(root.winfo_id())
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2


def check(label: str, ok: bool, detail: str = "") -> bool:
    ok = bool(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    return ok


def main() -> int:
    apply_dpi_awareness()
    results: list[bool] = []
    root, center = make_target_window()
    sender = InputSender()
    hook = CaptureHook()
    hook.start()
    temporary = tempfile.TemporaryDirectory()
    store = Store(Path(temporary.name))
    clicker = Clicker(sender=sender)
    runner = MacroRunner(store, sender=sender)
    done = threading.Event()
    outcome: list = []

    print(f"目标窗口中心：{center}")

    # 1) 键盘连点：F13 按下/松开各 5 次，应被钩子完整收到。
    hook.events.clear()
    clicker.start({"kind": "key", "interval": 0.02, "count": 5, "key": "F13",
                   "position": False, "x": 0, "y": 0},
                  lambda ok, msg: (outcome.append((ok, msg)), done.set()))
    got = hook.wait_for(lambda e: len([x for x in e if x[0] == "key"]) >= 10, timeout=5)
    keys = [x for x in hook.events if x[0] == "key"]
    vk_ok = all(k[1]["vk"] == VK_F13 for k in keys)
    injected = all(k[1]["injected"] for k in keys)
    results.append(check("键盘连点注入", got and done.wait(3) and vk_ok and injected,
                         f"收到 {len(keys)} 个按键事件，键码正确={vk_ok}，注入标记={injected}"))
    results.append(check("连点完成回调", outcome and outcome[-1][0],
                         outcome[-1][1] if outcome else "没有收到回调"))

    # 2) 鼠标连点：固定坐标点击 3 次，钩子应看到 6 个事件且坐标正确。
    hook.events.clear()
    outcome.clear()
    done.clear()
    clicker.start({"kind": "left", "interval": 0.05, "count": 3, "key": "F13",
                   "position": True, "x": center[0], "y": center[1]},
                  lambda ok, msg: (outcome.append((ok, msg)), done.set()))
    got = hook.wait_for(lambda e: len([x for x in e if x[0] == "mouse" and x[1]["message"] in (0x0201, 0x0202)]) >= 6,
                        timeout=5)
    clicks = [x for x in hook.events if x[0] == "mouse" and x[1]["message"] in (0x0201, 0x0202)]
    coords_ok = all(abs(x[1]["x"] - center[0]) <= 2 and abs(x[1]["y"] - center[1]) <= 2 for x in clicks)
    results.append(check("鼠标定点连点注入", got and coords_ok,
                         f"收到 {len(clicks)} 个点击事件，实际 {sorted({(x[1]['x'], x[1]['y']) for x in clicks})} "
                         f"vs 期望 {center}"))

    # 3) 录制：安装钩子后移动光标，应记录到带时间间隔的 move 事件。
    record_done = threading.Event()
    record_info: list = []
    session = RecordingSession(lambda ok, msg: (record_info.append((ok, msg)), record_done.set()))
    session.start()
    for offset in (0, 30, -20, 10):
        sender.move(center[0] + offset, center[1] + offset)
        time.sleep(0.08)
    session.stop()
    record_done.wait(3)
    moves = [e for e in session.snapshot() if e["type"] == "move"]
    results.append(check("录制捕获鼠标轨迹", bool(moves),
                         f"{len(moves)} 个移动事件，间隔合计 {sum(e['dt'] for e in moves):.2f} 秒"))

    # 4) 持久化 + 宏回放：录制的轨迹存盘后由宏调用，光标应回到最后一个坐标。
    store.save("recording", "自检轨迹", {"events": session.snapshot(), "hotkey": ""})
    document = store.load("recording", "自检轨迹")
    results.append(check("录制文件读写一致", document["events"] == session.snapshot(),
                         f"{len(document['events'])} 个事件已写入 {store.recording_dir.name}/"))
    outcome.clear()
    done.clear()
    runner.start({"name": "自检宏", "repeat": 2,
                  "steps": [{"type": "recording", "name": "自检轨迹"},
                            {"type": "wait", "seconds": 0.01}]},
                 lambda ok, msg: (outcome.append((ok, msg)), done.set()))
    finished = done.wait(10)
    last = moves[-1]
    position = sender.cursor_position()
    results.append(check("宏调用录制并回放", finished and outcome and outcome[0][0],
                         f"{outcome[0][1] if outcome else '未回调'}；光标 {position} vs 期望 ({last['x']}, {last['y']})"))
    results.append(check("回放后光标落在录制终点",
                         abs(position[0] - last["x"]) <= 2 and abs(position[1] - last["y"]) <= 2))

    # 5) 全局快捷键：注册 Ctrl+Alt+F12 并注入该组合键，回调应被触发。
    from clicker.win32 import HotkeyListener
    fired = threading.Event()
    listener = HotkeyListener(lambda action: fired.set() if action == "test" else None)
    chord = (0x11, 0x12, 0x7B)  # Ctrl、Alt、F12
    try:
        listener.set_hotkeys({"test": "Ctrl+Alt+F12"})
        for vk in chord:
            sender.key(vk, True)
            time.sleep(0.02)
        for vk in reversed(chord):
            sender.key(vk, False)
            time.sleep(0.02)
        results.append(check("全局快捷键触发", fired.wait(3), "已注册并注入 Ctrl+Alt+F12"))
    except Exception as exc:
        results.append(check("全局快捷键触发", False, str(exc)))
    finally:
        listener.stop()

    clicker.stop()
    runner.stop()
    hook.stop()
    root.destroy()
    temporary.cleanup()

    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} 项通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
