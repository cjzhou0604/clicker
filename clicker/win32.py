"""Windows 输入、消息循环和全局键盘钩子（不依赖第三方模块）。"""
from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes

if os.name != "nt":
    raise OSError("本程序只能在 Windows 上运行")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = wintypes.WPARAM
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_HOTKEY = 0x0312
INPUT_KEYBOARD = 1
INPUT_MOUSE = 0
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOD_NOREPEAT = 0x4000


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [
    wintypes.LPVOID, wintypes.HWND, wintypes.UINT, wintypes.UINT
]
user32.GetMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [
    wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT
]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = wintypes.SHORT
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.SetProcessDPIAware.restype = wintypes.BOOL
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL


def apply_dpi_awareness() -> str:
    """按物理像素渲染界面，使界面清晰且坐标与系统其他程序一致。"""
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor"
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()
        return "system"

# F1-F24、数字键盘、方向键及常见控制键。普通字符直接使用其 VK 码。
VK_NAMES = {f"F{i}": 0x6F + i for i in range(1, 25)}
VK_NAMES.update({
    "BACKSPACE": 0x08, "TAB": 0x09, "ENTER": 0x0D, "SHIFT": 0x10,
    "CTRL": 0x11, "ALT": 0x12, "PAUSE": 0x13, "CAPSLOCK": 0x14,
    "ESC": 0x1B, "SPACE": 0x20, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "END": 0x23, "HOME": 0x24, "LEFT": 0x25, "UP": 0x26,
    "RIGHT": 0x27, "DOWN": 0x28, "PRINTSCREEN": 0x2C, "INSERT": 0x2D,
    "DELETE": 0x2E, "WIN": 0x5B, "NUMLOCK": 0x90, "SCROLLLOCK": 0x91,
    "LSHIFT": 0xA0, "RSHIFT": 0xA1, "LCTRL": 0xA2, "RCTRL": 0xA3,
    "LALT": 0xA4, "RALT": 0xA5, "NUMPAD0": 0x60, "NUMPAD1": 0x61,
    "NUMPAD2": 0x62, "NUMPAD3": 0x63, "NUMPAD4": 0x64,
    "NUMPAD5": 0x65, "NUMPAD6": 0x66, "NUMPAD7": 0x67,
    "NUMPAD8": 0x68, "NUMPAD9": 0x69, "MULTIPLY": 0x6A, "ADD": 0x6B,
    "SUBTRACT": 0x6D, "DECIMAL": 0x6E, "DIVIDE": 0x6F,
    "VOLUME_MUTE": 0xAD, "VOLUME_DOWN": 0xAE, "VOLUME_UP": 0xAF,
})
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    VK_NAMES[_c] = ord(_c)
for _name, _vk in zip(
    ("SEMICOLON", "EQUALS", "COMMA", "MINUS", "PERIOD", "SLASH", "BACKTICK",
     "LBRACKET", "BACKSLASH", "RBRACKET", "QUOTE"),
    (0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF, 0xC0, 0xDB, 0xDC, 0xDD, 0xDE),
):
    VK_NAMES[_name] = _vk

NAME_BY_VK = {vk: name for name, vk in VK_NAMES.items() if len(name) == 1}
NAME_BY_VK.update({
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x10: "Shift",
    0x11: "Ctrl", 0x12: "Alt", 0x13: "Pause", 0x14: "CapsLock",
    0x1B: "Esc", 0x20: "Space", 0x21: "PageUp", 0x22: "PageDown",
    0x23: "End", 0x24: "Home", 0x25: "←", 0x26: "↑", 0x27: "→",
    0x28: "↓", 0x2C: "PrintScreen", 0x2D: "Insert", 0x2E: "Delete",
    0x5B: "Win", 0x5C: "Win", 0x90: "NumLock", 0x91: "ScrollLock",
    0xA0: "LShift", 0xA1: "RShift", 0xA2: "LCtrl", 0xA3: "RCtrl",
    0xA4: "LAlt", 0xA5: "RAlt", 0xAD: "VolumeMute", 0xAE: "VolumeDown",
    0xAF: "VolumeUp", 0x6A: "NumPad*", 0x6B: "NumPad+", 0x6D: "NumPad-",
    0x6E: "NumPad.", 0x6F: "NumPad/", 0xBA: ";", 0xBB: "=", 0xBC: ",",
    0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\",
    0xDD: "]", 0xDE: "'",
})
NAME_BY_VK.update({0x60 + i: f"NumPad{i}" for i in range(10)})

EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
    0x5B, 0x5C, 0xA3, 0xA5, 0x6F,
}


def normalize_hotkey(value: str) -> tuple[int, int, str]:
    """将 ``Ctrl+Shift+F8`` 转换为 RegisterHotKey 所需参数。"""
    tokens = [p.strip() for p in value.upper().replace("-", "+").split("+") if p.strip()]
    if not tokens:
        raise ValueError("快捷键不能为空")
    modifiers = 0
    aliases = {"CONTROL": "CTRL", "WINDOWS": "WIN"}
    keys = []
    for token in tokens:
        token = aliases.get(token, token)
        if token in ("CTRL", "ALT", "SHIFT", "WIN"):
            modifiers |= {"CTRL": 2, "ALT": 1, "SHIFT": 4, "WIN": 8}[token]
        else:
            keys.append(token)
    if len(keys) != 1 or keys[0] not in VK_NAMES:
        raise ValueError("快捷键应为一个按键，可加 Ctrl、Alt、Shift、Win，例如 Ctrl+F8")
    vk = VK_NAMES[keys[0]]
    return vk, modifiers, "+".join(
        [n for n, bit in (("Ctrl", 2), ("Alt", 1), ("Shift", 4), ("Win", 8)) if modifiers & bit]
        + [keys[0]]
    )


def key_name(vk: int) -> str:
    return NAME_BY_VK.get(vk, f"VK_{vk}")


# 修饰键的显示顺序与 normalize_hotkey 一致。
MODIFIER_KEYS = (("Ctrl", (0x11,)), ("Alt", (0x12,)), ("Shift", (0x10,)),
                 ("Win", (0x5B, 0x5C)))


def pressed_modifiers() -> list[str]:
    """当前真实按住的修饰键名。

    Tk 的 event.state 只要收到过修饰键的按下就会置位，Alt+Tab 之类让它错过松开
    事件时会一直残留，因此这里直接读系统的实时按键状态。
    """
    return [name for name, vks in MODIFIER_KEYS
            if any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks)]


class InputSender:
    """使用 SendInput 生成操作系统输入事件。"""

    def mouse_button(self, button: str, down: bool) -> None:
        flags = {
            "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
            "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
        }[button]
        self._mouse(flags[0 if down else 1], 0)

    def wheel(self, direction: str) -> None:
        self._mouse(MOUSEEVENTF_WHEEL, 120 if direction == "up" else -120)

    def move(self, x: int, y: int) -> None:
        if not user32.SetCursorPos(int(x), int(y)):
            raise ctypes.WinError(ctypes.get_last_error())

    def key(self, vk: int, down: bool) -> None:
        scan = user32.MapVirtualKeyW(vk, 0)
        flags = KEYEVENTF_SCANCODE if scan else 0
        if vk in EXTENDED_VKS:
            flags |= KEYEVENTF_EXTENDEDKEY
        if not down:
            flags |= KEYEVENTF_KEYUP
        # 有扫描码时按扫描码注入（游戏等直接读扫描码的程序才能收到），
        # F13 这类没有扫描码的键则退回按虚拟键码注入。
        item = INPUT(type=INPUT_KEYBOARD)
        item.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        self._send(item)

    def text(self, text: str) -> None:
        """按 UTF-16 码元发送 Unicode，不依赖输入法或剪贴板。"""
        for character in text:
            encoded = character.encode("utf-16-le", "surrogatepass")
            for i in range(0, len(encoded), 2):
                code = int.from_bytes(encoded[i:i + 2], "little")
                for down in (True, False):
                    item = INPUT(type=INPUT_KEYBOARD)
                    item.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE | (0 if down else KEYEVENTF_KEYUP), 0, 0)
                    self._send(item)

    @staticmethod
    def cursor_position() -> tuple[int, int]:
        point = POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            raise ctypes.WinError(ctypes.get_last_error())
        return point.x, point.y

    @staticmethod
    def key_is_down(vk: int) -> bool:
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    @staticmethod
    def window_rect(hwnd: int) -> tuple[int, int, int, int]:
        """窗口的物理像素矩形 (左, 上, 右, 下)，可直接和 cursor_position 比较。"""
        rect = wintypes.RECT()
        if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return rect.left, rect.top, rect.right, rect.bottom

    @staticmethod
    def foreground_process() -> int:
        hwnd = user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        if hwnd:
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _mouse(self, flags: int, data: int) -> None:
        item = INPUT(type=INPUT_MOUSE)
        item.mi = MOUSEINPUT(0, 0, ctypes.c_uint32(data).value, flags, 0, 0)
        self._send(item)

    @staticmethod
    def _send(item: INPUT) -> None:
        if user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT)) != 1:
            raise ctypes.WinError(ctypes.get_last_error())


class KeyboardHook:
    """底层键盘钩子只负责投递事件，不执行录制或播放，避免阻塞系统钩子。"""

    def __init__(self, on_event):
        self.on_event = on_event
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.ready = threading.Event()
        self.error: str | None = None
        self._hook = None
        self._callback = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.ready.clear()
        self.error = None
        self.thread = threading.Thread(target=self._run, name="键盘全局钩子", daemon=True)
        self.thread.start()
        self.ready.wait(5)
        if self.error:
            raise RuntimeError(self.error)

    def stop(self) -> None:
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(2)
        self.thread = None
        self.thread_id = 0

    def _run(self) -> None:
        self.thread_id = kernel32.GetCurrentThreadId()

        @HOOKPROC
        def callback(code, wparam, lparam):
            if code >= 0:
                data = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not data.flags & 0x10:  # 不捕获 SendInput 生成的 LLKHF_INJECTED 事件
                    try:
                        self.on_event(
                            int(data.vkCode), int(data.scanCode),
                            wparam in (WM_KEYDOWN, WM_SYSKEYDOWN),
                        )
                    except Exception:
                        pass  # 永远不把 Python 异常抛回 Windows 钩子
            return user32.CallNextHookEx(self._hook, code, wparam, lparam)

        self._callback = callback  # 保持 C 回调对象存活
        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, callback, kernel32.GetModuleHandleW(None), 0
        )
        if not self._hook:
            self.error = f"无法安装键盘全局钩子：{ctypes.WinError(ctypes.get_last_error())}"
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


class HotkeyListener:
    """RegisterHotKey 全局热键；热键不会被目标程序接收。"""

    def __init__(self, on_hotkey):
        self.on_hotkey = on_hotkey
        self._registrations: dict[int, int] = {}
        self._next_id = 1
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._queue = None

    def set_hotkeys(self, entries: dict[int, str]) -> None:
        parsed = {}
        used = set()
        for key, value in entries.items():
            if not value:
                continue
            vk, mods, _ = normalize_hotkey(value)
            if (vk, mods) in used:
                raise ValueError("多个操作不能使用同一个快捷键")
            used.add((vk, mods))
            parsed[key] = (vk, mods)
        self.stop()
        if not parsed:
            return
        import queue
        self._queue = queue.Queue()
        self._registrations = parsed
        self._thread = threading.Thread(target=self._run, name="全局快捷键", daemon=True)
        self._thread.start()
        result = self._queue.get(timeout=5)
        if isinstance(result, Exception):
            self.stop()
            raise result

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(2)
        self._thread = None
        self._thread_id = 0

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        registered = []
        try:
            for action, (vk, mods) in self._registrations.items():
                action_id = self._next_id
                self._next_id += 1
                if not user32.RegisterHotKey(None, action_id, mods | MOD_NOREPEAT, vk):
                    error = ctypes.get_last_error()
                    raise RuntimeError(
                        f"快捷键注册失败（可能已被其他程序占用）：{ctypes.WinError(error)}"
                    )
                registered.append(action_id)
            self._queue.put(True)
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY:
                    index = registered.index(message.wParam)
                    self.on_hotkey(list(self._registrations)[index])
        except Exception as exc:
            self._queue.put(exc)
        finally:
            for action_id in registered:
                user32.UnregisterHotKey(None, action_id)
