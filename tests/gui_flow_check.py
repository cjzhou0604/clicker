"""界面流程自检（需要桌面会话，手动运行：python tests/gui_flow_check.py）。

直接驱动 App 的界面方法，验证「连点 / 录制 / 宏」三条链路与本地文件保存，
不依赖 Computer Use，也不操作本程序以外的窗口。全局热键留空，避免与
正在运行的界面实例抢注同一个按键。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 控制台默认可能是 GBK，状态文本里会出现 ⚠ 等非 GBK 字符。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from clicker.app import App, event_label, format_seconds, hotkey_from_event, step_label
from clicker.store import Store
from clicker.win32 import apply_dpi_awareness

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    ok = bool(ok)
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")


def pump(app: App, seconds: float = 0.5, until=None) -> bool:
    """在等待后台线程的同时保持 Tk 事件循环运转。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.update()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until() if until is not None else False


def main() -> int:
    apply_dpi_awareness()
    temporary = tempfile.TemporaryDirectory()
    data_dir = Path(temporary.name) / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "config.json").write_text(json.dumps({
        "version": 1, "hotkey": "", "stop_hotkey": "", "playback_hotkey": "",
        "click": {"kind": "key", "interval": 0.02, "key": "F13", "count": 2,
                  "position": False, "x": 0, "y": 0},
    }, ensure_ascii=False), encoding="utf-8")
    app = App(Store(data_dir))
    pump(app, 0.3)

    # 1) 连点：键盘 F13 两次，0.02 秒间隔。
    check("连点页读取本地配置",
          app.click_kind.get() == "key" and app.click_count.get() == "2"
          and app.primary_hotkey.get() == "",
          f"{step_label({'type': 'key', 'key': app.click_key.get()})}，间隔 {format_seconds(app.interval_seconds())} 秒")
    app.toggle_click()
    pump(app, 3, until=lambda: "连点" in app.status.get() and "启动" not in app.status.get())
    check("连点执行完成", "连点完成" in app.status.get(), app.status.get())

    # 2) 录制：启停一次，期间移动光标产生事件。
    app.toggle_record()
    check("录制钩子安装成功", app.recording.running, app.status.get())
    origin = app.sender.cursor_position()
    for offset in (40, -30, 15):
        app.sender.move(origin[0] + offset, origin[1] + offset)
        pump(app, 0.15)
    app.toggle_record()
    pump(app, 1.5, until=lambda: not app.recording.running)
    events = app.recording.snapshot()
    check("录制捕获到事件", bool(events), f"{len(events)} 个事件，例如 {event_label(events[0]) if events else '无'}")
    app.rec_name.set("界面测试")
    app.rec_hotkey.set("Ctrl+Alt+F14")
    app.save_recording()
    saved = data_dir / "recordings" / "界面测试.json"
    check("录制已写入本文件夹", saved.exists(), str(saved))
    document = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else {}
    check("录制快捷键随文件保存", document.get("hotkey") == "Ctrl+Alt+F14", str(document.get("hotkey")))

    # 3) 宏：调用上面的录制 + 等待，保存后运行。
    app.new_macro()
    app.step_type.set("调用已录制操作")
    app._update_step_form()
    app.step_recording.set("界面测试")
    app.add_step()
    app.step_type.set("等待")
    app._update_step_form()
    app.step_seconds.set("0.05")
    app.add_step()
    check("宏步骤可描述", len(app.steps) == 2, " / ".join(step_label(s) for s in app.steps))
    app.macro_name.set("界面宏")
    app.macro_repeat.set("2")
    app.save_macro()
    macro_file = data_dir / "macros" / "界面宏.json"
    check("宏已写入本文件夹", macro_file.exists(), str(macro_file))
    app.macro_list.selection_set("界面宏")
    app._load_macro_selection()
    check("宏可从列表重新载入", len(app.steps) == 2 and app.macro_repeat.get() == "2")
    app.toggle_selected_macro()
    pump(app, 8, until=lambda: "宏" in app.status.get() and "完成" in app.status.get())
    check("宏运行完成", "完成" in app.status.get(), app.status.get())

    # 4) 快捷键冲突提示（不实际注册，避免与已运行实例抢键）。
    app.primary_hotkey.set("Ctrl+Alt+F14")
    app.stop_hotkey.set("Ctrl+Alt+F14")
    app.refresh_hotkeys()
    check("重复快捷键被拒绝", "同时被" in app.status.get(), app.status.get())
    app.primary_hotkey.set("")
    app.stop_hotkey.set("")

    # 5) 选项页：延时播放倒计时、期间置灰、可取消，最小化后播完还原。
    check("选项页已加入页签", app.notebook.index("end") == 4, str(app.notebook.index("end")))

    def hotkey_tab() -> str:
        """启停热键输入框当前挂在哪个页签下。"""
        target = app.primary_hotkey._name        # tkinter 只暴露这个 Tcl 变量名
        for name in ("click_tab", "record_tab", "macro_tab", "options_tab"):
            stack = list(getattr(app, name).winfo_children())
            while stack:
                widget = stack.pop()
                if widget.winfo_class() == "TEntry" and widget.cget("textvariable") == target:
                    return name
                stack.extend(widget.winfo_children())
        return "无"

    check("启停热键输入框在选项页", hotkey_tab() == "options_tab", hotkey_tab())
    app.notebook.select(3)
    app._primary_action()
    check("选项页按启停热键给出说明", "选项页" in app.status.get(), app.status.get())
    app.notebook.select(1)

    app.rec_list.selection_set("界面测试")
    app.playback_delay.set("2")
    app.play_selected()
    pump(app, 0.3)
    check("延时播放进入倒计时", app._pending is not None and "秒后播放" in app.status.get(),
          app.status.get())
    check("倒计时期间按钮置灰",
          str(app.rec_play["state"]) == "disabled" and str(app.rec_stop["state"]) == "normal",
          f"播放选中={app.rec_play['state']} 停止播放={app.rec_stop['state']}")
    app.stop_playback()
    check("倒计时可被取消", app._pending is None and "取消" in app.status.get(), app.status.get())
    pump(app, 0.3)
    check("取消后按钮恢复可用", str(app.rec_play["state"]) == "normal", str(app.rec_play["state"]))

    app.playback_delay.set("0")
    app.playback_minimize.set(True)
    app.play_selected()
    pump(app, 1, until=lambda: app.state() == "iconic")
    check("播放前按选项最小化", app.state() == "iconic", app.state())
    pump(app, 5, until=lambda: "播放完成" in app.status.get())
    check("播放完成有明确反馈", "播放完成" in app.status.get(), app.status.get())
    check("播完自动还原窗口", app.state() == "normal", app.state())
    app.playback_minimize.set(False)

    # 6) 取光标位置：按下按钮后等用户在别处真实点一下，点自己窗口不算。
    app.click_position.set(True)
    app._on_position_toggle()
    app.click_x.set("0")
    app.click_y.set("0")
    app._pick_position()
    left, top, right, bottom = app.sender.window_rect(app.winfo_id())
    check("取坐标进入等待点击状态",
          app._pick_button is app.pick_button and app.pick_button.cget("text") == "请点选位置…",
          f"{app.pick_button.cget('text')} / 窗口 {left},{top},{right},{bottom}")
    app._finish_pick(((left + right) // 2, (top + bottom) // 2))
    check("点在本窗口上不改变坐标",
          app._pick_button is app.pick_button and app.click_x.get() == "0",
          f"{app.click_x.get()},{app.click_y.get()}")
    app._finish_pick((left - 10, top - 10))
    check("别处点击写回坐标",
          app._pick_button is None and app.click_x.get() == str(left - 10)
          and app.click_y.get() == str(top - 10)
          and app.pick_button.cget("text") == "取光标位置",
          f"{app.click_x.get()},{app.click_y.get()} / {app.status.get()}")
    app._pick_position()
    app._pick_deadline = time.monotonic() - 1
    pump(app, 0.5)
    check("取坐标超时自动取消",
          app._pick_button is None and "超时" in app.status.get(), app.status.get())
    app._pick_position()
    app._pick_position()
    check("再按一次取消取坐标",
          app._pick_button is None and "取消" in app.status.get(), app.status.get())
    app.click_position.set(False)
    app._on_position_toggle()

    # 7) 纯函数：组合键捕获与等待步骤描述。
    class Event:
        keysym = "F8"
        state = 8  # Tk 错过 Alt 松开事件后会一直留着这一位

    check("组合键捕获格式", hotkey_from_event(Event(), ["Ctrl"]) == "Ctrl+F8",
          hotkey_from_event(Event(), ["Ctrl"]))
    check("残留修饰键状态被忽略", hotkey_from_event(Event()) == "F8",
          str(hotkey_from_event(Event())))
    check("等待步骤描述", step_label({"type": "wait", "seconds": 1.5}) == "等待 1.5 秒",
          step_label({"type": "wait", "seconds": 1.5}))

    app.on_close()
    temporary.cleanup()
    passed = sum(results)
    print(f"\n{passed}/{len(results)} 项通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
