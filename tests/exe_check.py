"""校验打包出的 exe：窗口、数据落点、尺寸、DPI 感知、图标与干净退出。

脚本把 exe 复制到临时目录再启动，以此证明 data 写在 exe 同级目录，而不是
PyInstaller 的临时解压目录；结束时用 WM_CLOSE 请求退出，不强杀进程。
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

TITLE = "连点器"          # 窗口标题保持中文，改成 ASCII 的只是文件名
WS_CAPTION = 0x00C00000
WM_CLOSE = 0x0010
DPI_AWARENESS = {0: "unaware", 1: "system", 2: "per-monitor", 3: "per-monitor-v2"}


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
ResourceEnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_long)

for name, restype, args in (
    ("GetWindowTextLengthW", ctypes.c_int, (wintypes.HWND,)),
    ("GetWindowThreadProcessId", wintypes.DWORD, (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))),
    ("GetWindowLongW", wintypes.LONG, (wintypes.HWND, ctypes.c_int)),
    ("IsWindowVisible", wintypes.BOOL, (wintypes.HWND,)),
    ("GetWindowRect", wintypes.BOOL, (wintypes.HWND, ctypes.POINTER(RECT))),
    ("PostMessageW", wintypes.BOOL, (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)),
    ("GetSystemMetrics", ctypes.c_int, (ctypes.c_int,)),
    ("GetWindowDpiAwarenessContext", ctypes.c_void_p, (wintypes.HWND,)),
    ("GetAwarenessFromDpiAwarenessContext", ctypes.c_int, (ctypes.c_void_p,)),
    ("GetDpiForWindow", wintypes.UINT, (wintypes.HWND,)),
    ("SetProcessDpiAwarenessContext", wintypes.BOOL, (ctypes.c_void_p,)),
):
    function = getattr(user32, name)
    function.restype = restype
    function.argtypes = args

for name, restype, args in (
    ("OpenProcess", wintypes.HANDLE, (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)),
    ("QueryFullProcessImageNameW", wintypes.BOOL,
     (wintypes.HANDLE, wintypes.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD))),
    ("CloseHandle", wintypes.BOOL, (wintypes.HANDLE,)),
    ("LoadLibraryExW", wintypes.HINSTANCE, (wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD)),
    ("FindResourceW", ctypes.c_void_p, (wintypes.HINSTANCE, ctypes.c_void_p, ctypes.c_void_p)),
    ("LoadResource", ctypes.c_void_p, (wintypes.HINSTANCE, ctypes.c_void_p)),
    ("SizeofResource", wintypes.DWORD, (wintypes.HINSTANCE, ctypes.c_void_p)),
    ("EnumResourceNamesW", wintypes.BOOL,
     (wintypes.HMODULE, ctypes.c_void_p, ResourceEnumProc, ctypes.c_long)),
    ("FreeLibrary", wintypes.BOOL, (wintypes.HINSTANCE,)),
):
    function = getattr(kernel32, name)
    function.restype = restype
    function.argtypes = args


def titled_windows(title: str) -> dict[int, int]:
    """返回 {PID: HWND}，只认带标题栏的主窗口。"""
    found: dict[int, int] = {}

    def callback(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value != title or not user32.GetWindowLongW(hwnd, -16) & WS_CAPTION:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found[pid.value] = hwnd
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return found


def process_image(pid: int) -> str:
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(4096)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def embedded_icon_sizes(exe: Path) -> list[int]:
    """读取 exe 内第一个 RT_GROUP_ICON，返回它包含的图标边长。"""
    module = kernel32.LoadLibraryExW(str(exe), None, 0x10)  # LOAD_LIBRARY_AS_DATAFILE
    if not module:
        return []
    try:
        ids: list[int] = []
        # RT_GROUP_ICON 的编号由 PyInstaller 分配，用回调枚举而不是猜固定值。
        callback = ResourceEnumProc(
            lambda _m, _t, name, _l: ids.append(name if isinstance(name, int)
                                               else ctypes.cast(name, ctypes.c_wchar_p).value) or True)
        kernel32.EnumResourceNamesW(module, ctypes.c_void_p(14), callback, 0)
        for group_id in ids:
            handle = kernel32.FindResourceW(module, ctypes.c_void_p(group_id), ctypes.c_void_p(14))
            if not handle:
                continue
            data = kernel32.LoadResource(module, handle)
            if not data:
                continue
            raw = ctypes.string_at(data, kernel32.SizeofResource(module, handle))
            count = int.from_bytes(raw[4:6], "little")
            return [raw[6 + index * 14] or 256 for index in range(count)]
        return []
    finally:
        kernel32.FreeLibrary(module)


def main() -> int:
    # 检查器自己按物理像素工作，否则屏幕与窗口尺寸都会被系统缩放。
    if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        user32.SetProcessDPIAware()
    default = Path(__file__).resolve().parent.parent / "Clicker.exe"
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    if not source.is_file():
        raise SystemExit(f"找不到 exe：{source}")

    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))

    temporary = Path(tempfile.mkdtemp(prefix="clicker-exe-"))
    copy = temporary / source.name
    shutil.copy2(source, copy)
    internal = source.parent / "_internal"
    if internal.is_dir():                       # onedir：运行时目录必须与 exe 同级
        shutil.copytree(internal, temporary / "_internal")
    known = set(titled_windows(TITLE))
    temp_root = Path(os.path.expandvars(r"%TEMP%"))
    mei_before = {p.name for p in temp_root.glob("_MEI*")}
    proc = subprocess.Popen([str(copy)], cwd=str(temporary))
    hwnd = 0
    try:
        deadline = time.monotonic() + 60          # 单文件包要先解压，留足启动时间
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            fresh = {pid: window for pid, window in titled_windows(TITLE).items() if pid not in known}
            if fresh:
                hwnd = next(iter(fresh.values()))
                break
            time.sleep(0.25)

        check("exe 启动并创建窗口", hwnd != 0,
              f"HWND={hwnd:#x}" if hwnd else f"进程退出码 {proc.returncode}")
        if not hwnd:
            return report(results)

        # onedir 不该碰 %TEMP%；这条专门盯着「Could not create temporary directory!」那类问题。
        mei_after = {p.name for p in temp_root.glob("_MEI*")} - mei_before
        check("运行期间未在 %TEMP% 建解压目录", not mei_after,
              f"新增 {sorted(mei_after)}" if mei_after else f"{temp_root} 无变化")

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not (temporary / "data" / "macros").is_dir():
            time.sleep(0.2)
        created = sorted(p.relative_to(temporary).as_posix() for p in temporary.iterdir())
        check("记录目录写在 exe 同级", (temporary / "data" / "recordings").is_dir()
              and (temporary / "data" / "macros").is_dir(), str(created))

        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        screen = user32.GetSystemMetrics(0)
        check("窗口尺寸合理且未被裁切", 800 <= width <= screen and 500 <= height,
              f"{width}x{height}，屏幕宽 {screen}")

        awareness = user32.GetAwarenessFromDpiAwarenessContext(
            user32.GetWindowDpiAwarenessContext(hwnd))
        # 引导清单先声明了 per-monitor，Python 里再设 per-monitor-v2 会被系统拒绝。
        check("进程按物理像素渲染（per-monitor 及以上）", awareness in (2, 3),
              f"{DPI_AWARENESS.get(awareness, awareness)}，窗口 DPI={user32.GetDpiForWindow(hwnd)}")

        sizes = embedded_icon_sizes(source)
        check("自定义多尺寸图标已嵌入 exe", len(sizes) >= 3, f"含 {sizes} 像素")

        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        try:
            code: int | None = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            code = None
        check("关闭窗口后进程干净退出", code == 0, f"退出码 {code}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)
        shutil.rmtree(temporary, ignore_errors=True)

    return report(results)


def report(results: list[tuple[str, bool, str]]) -> int:
    failed = [name for name, ok, _ in results if not ok]
    print(f"\n共 {len(results)} 项，通过 {len(results) - len(failed)} 项"
          + (f"，失败：{failed}" if failed else "，全部通过"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
