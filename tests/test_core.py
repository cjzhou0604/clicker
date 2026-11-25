"""Store、Clicker 和 MacroRunner 的确定性单元测试。

所有输入都发送给 FakeSender；测试不会调用 SendInput、全局钩子或鼠标键盘。
"""
from __future__ import annotations

import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from clicker.engine import Clicker, MacroRunner
from clicker.store import DEFAULT_CONFIG, SCHEMA_VERSION, Store, validate_event
from clicker.win32 import normalize_hotkey


class FakeSender:
    """线程安全地记录输入，并允许测试等待后台线程抵达指定位置。"""

    def __init__(self):
        self.calls = []
        self._condition = threading.Condition()

    def _record(self, call):
        with self._condition:
            self.calls.append(call)
            self._condition.notify_all()

    def mouse_button(self, button, down):
        self._record(("mouse_button", button, down))

    def wheel(self, direction):
        self._record(("wheel", direction))

    def move(self, x, y):
        self._record(("move", x, y))

    def key(self, vk, down):
        self._record(("key", vk, down))

    def text(self, text):
        self._record(("text", text))

    def wait_for_calls(self, count, timeout=2):
        with self._condition:
            arrived = self._condition.wait_for(lambda: len(self.calls) >= count, timeout)
            if not arrived:
                raise AssertionError(f"只收到 {len(self.calls)} 个调用，预期至少 {count} 个")
            return list(self.calls)


class FinishCallback:
    def __init__(self):
        self.calls = []
        self.done = threading.Event()

    def __call__(self, ok, message):
        self.calls.append((ok, message))
        self.done.set()

    def result(self, timeout=2):
        if not self.done.wait(timeout):
            raise AssertionError("后台任务未及时调用完成回调")
        return self.calls[0]


class RecordingSessionTests(unittest.TestCase):
    """只验证事件收集逻辑，不安装系统钩子。"""

    def session(self, ignore=()):
        from clicker.engine import RecordingSession

        return RecordingSession(lambda ok, message: None, ignore_vk=ignore)

    def test_ignored_hotkey_is_not_recorded_and_delays_accumulate(self):
        session = self.session(ignore=[0x77])
        session._running = True
        session._started = session._last_time = time.perf_counter()

        session.key_event(0x77, 0, True)   # 启停热键本身
        session.key_event(0x77, 0, False)
        session.key_event(ord("A"), 0, True)
        session.mouse_event("wheel", delta=-120)

        self.assertEqual([event["type"] for event in session.snapshot()], ["key_down", "wheel"])
        self.assertEqual(session.count(), 2)
        self.assertGreaterEqual(session.snapshot()[1]["dt"], 0.0)
        self.assertTrue(session.snapshot()[0]["dt"] <= session.snapshot()[1]["dt"])

    def test_snapshot_returns_copies_after_stop(self):
        session = self.session()
        session._running = True
        session._started = session._last_time = time.perf_counter()
        session.mouse_event("mouse_down", button="left")
        events = session.snapshot()
        events[0]["type"] = "tampered"

        self.assertEqual(session.snapshot()[0]["type"], "mouse_down")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name) / "data"
        self.store = Store(self.data_dir)

    def test_config_defaults_are_returned_without_creating_config_file(self):
        config = self.store.load_config()

        self.assertEqual(config, DEFAULT_CONFIG)
        self.assertIsNot(config, DEFAULT_CONFIG)
        self.assertIsNot(config["click"], DEFAULT_CONFIG["click"])
        self.assertFalse(self.store.config_file.exists())
        self.assertTrue(self.store.recording_dir.is_dir())
        self.assertTrue(self.store.macro_dir.is_dir())

        config["click"]["interval"] = 2
        self.assertEqual(self.store.load_config()["click"]["interval"], 0.1)

    def test_config_save_and_load_round_trip(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        config.update(
            hotkey="Ctrl+F8",
            stop_hotkey="Alt+F9",
            playback_hotkey="Shift+F7",
            last_recording="示例",
        )
        config["click"].update(kind="key", key="B", interval=0.25, count=7)

        self.store.save_config(config)

        self.assertEqual(self.store.load_config(), config)
        with self.store.config_file.open(encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), config)

    def test_playback_options_round_trip_and_validate(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["playback"] = {"minimize": True, "delay": 2.5}

        self.store.save_config(config)

        self.assertEqual(self.store.load_config()["playback"], {"minimize": True, "delay": 2.5})
        for playback in (
            {}, {"minimize": "yes", "delay": 1}, {"minimize": True, "delay": -0.5},
            {"minimize": True, "delay": 3601}, {"minimize": True, "delay": True},
            {"minimize": True, "delay": "1"},
        ):
            with self.subTest(playback=playback):
                broken = copy.deepcopy(DEFAULT_CONFIG)
                broken["playback"] = playback
                with self.assertRaises(ValueError):
                    self.store.save_config(broken)

    def test_invalid_names_are_rejected(self):
        invalid_names = (
            "", "   ", ".", "..", "../escape", "a/b", "a\\b", "a:b",
            "a?b", "a*b", "line\nbreak", "x" * 61,
        )
        for name in invalid_names:
            with self.subTest(name=repr(name)):
                with self.assertRaises(ValueError):
                    self.store.save("recording", name, {"events": []})
                with self.assertRaises(ValueError):
                    self.store.save(
                        "macro", name, {"steps": [{"type": "wait", "seconds": 0}]}
                    )
        self.assertEqual(list(self.data_dir.rglob("*.json")), [])

    def test_recording_persists_and_load_does_not_rewrite(self):
        events = [
            {"type": "mouse_down", "dt": 0, "button": "left"},
            {"type": "mouse_up", "dt": 0.01, "button": "left"},
            {"type": "wheel", "dt": 0, "delta": 240},
            {"type": "key_down", "dt": 0, "vk": 65},
            {"type": "key_up", "dt": 0, "vk": 65},
            {"type": "move", "dt": 0, "x": 10, "y": -20},
        ]
        document = self.store.save("recording", "录制 1", {"events": events})
        path = self.store.recording_dir / "录制 1.json"
        before = path.read_bytes()
        before_mtime = path.stat().st_mtime_ns

        self.assertEqual(document["version"], SCHEMA_VERSION)
        self.assertEqual(document["type"], "recording")
        self.assertEqual(self.store.list_names("recording"), ["录制 1"])
        with mock.patch.object(
            self.store, "_write_json", side_effect=AssertionError("读取不应写文件")
        ):
            self.assertEqual(self.store.load("recording", "录制 1"), document)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, before_mtime)

    def test_macro_persists_and_load_does_not_rewrite(self):
        steps = [
            {"type": "recording", "name": "录制 1"},
            {"type": "click", "button": "right"},
            {"type": "text", "text": "你好"},
            {"type": "wait", "seconds": 0},
        ]
        document = self.store.save(
            "macro", "宏 1", {"steps": steps, "repeat": 3, "hotkey": "Ctrl+F6"}
        )
        path = self.store.macro_dir / "宏 1.json"
        before = path.read_bytes()
        before_mtime = path.stat().st_mtime_ns

        self.assertEqual(document["version"], SCHEMA_VERSION)
        self.assertEqual(document["type"], "macro")
        self.assertEqual(document["repeat"], 3)
        self.assertEqual(document["hotkey"], "Ctrl+F6")
        self.assertEqual(self.store.list_names("macro"), ["宏 1"])
        with mock.patch.object(
            self.store, "_write_json", side_effect=AssertionError("读取不应写文件")
        ):
            self.assertEqual(self.store.load("macro", "宏 1"), document)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, before_mtime)

    def test_recording_hotkey_round_trip(self):
        events = [{"type": "mouse_down", "dt": 0, "button": "left"}]

        saved = self.store.save("recording", "带快捷键", {"events": events, "hotkey": "Ctrl+Alt+F6"})
        loaded = self.store.load("recording", "带快捷键")

        self.assertEqual(saved["hotkey"], "Ctrl+Alt+F6")
        self.assertEqual(loaded, saved)
        self.assertEqual(self.store.save("recording", "无快捷键", {"events": events})["hotkey"], "")

    def test_invalid_events_are_rejected_by_validation_and_save(self):
        invalid_events = (
            None,
            {},
            {"type": "move", "x": 0, "y": 0},
            {"type": "move", "dt": -0.1, "x": 0, "y": 0},
            {"type": "mouse_down", "dt": 0, "button": "back"},
            {"type": "wheel", "dt": 0, "delta": 0},
            {"type": "wheel", "dt": 0, "delta": 121},
            {"type": "key_down", "dt": 0, "vk": 0},
            {"type": "key_up", "dt": 0, "vk": 255},
            {"type": "move", "dt": 0, "x": 1.5, "y": 0},
            {"type": "unknown", "dt": 0},
        )
        for event in invalid_events:
            with self.subTest(event=event):
                with self.assertRaises(ValueError):
                    validate_event(event)
                with self.assertRaises(ValueError):
                    self.store.save("recording", "invalid", {"events": [event]})
        self.assertFalse((self.store.recording_dir / "invalid.json").exists())


class HotkeyTests(unittest.TestCase):
    def test_normalize_hotkey_aliases_modifiers_and_display_name(self):
        cases = {
            "f8": (0x77, 0, "F8"),
            " control-shift-f7 ": (0x76, 2 | 4, "Ctrl+Shift+F7"),
            "Ctrl+Alt+Shift+Win+a": (ord("A"), 1 | 2 | 4 | 8, "Ctrl+Alt+Shift+Win+A"),
            "Windows+Enter": (0x0D, 8, "Win+ENTER"),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_hotkey(value), expected)

    def test_normalize_hotkey_rejects_invalid_values(self):
        for value in ("", "Ctrl", "A+B", "Ctrl+NotAKey", "Ctrl++"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_hotkey(value)


class ClickerTests(unittest.TestCase):
    def test_finite_clicking_honors_count_position_and_callback(self):
        sender = FakeSender()
        clicker = Clicker(sender=sender)
        self.addCleanup(clicker.stop)
        finished = FinishCallback()
        settings = {
            "kind": "left", "interval": 0.001, "count": 3, "key": "A",
            "position": True, "x": 11, "y": 22,
        }

        self.assertTrue(clicker.start(settings, finished))

        self.assertEqual(finished.result(), (True, "连点完成"))
        cycle = [
            ("move", 11, 22),
            ("mouse_button", "left", True),
            ("mouse_button", "left", False),
        ]
        self.assertEqual(sender.calls, cycle * 3)
        self.assertFalse(clicker.running)
        self.assertEqual(len(finished.calls), 1)

    def test_unlimited_clicking_can_be_stopped_during_wait(self):
        sender = FakeSender()
        clicker = Clicker(sender=sender)
        self.addCleanup(clicker.stop)
        finished = FinishCallback()
        settings = {
            "kind": "right", "interval": 60, "count": 0, "key": "A",
            "position": False, "x": 0, "y": 0,
        }

        self.assertTrue(clicker.start(settings, finished))
        sender.wait_for_calls(2)
        clicker.stop()

        self.assertEqual(finished.result(), (True, "连点已停止"))
        self.assertEqual(
            sender.calls,
            [("mouse_button", "right", True), ("mouse_button", "right", False)],
        )
        self.assertFalse(clicker.running)

    def test_short_interval_is_not_rounded_to_system_tick(self):
        sender = FakeSender()
        clicker = Clicker(sender=sender)
        self.addCleanup(clicker.stop)
        finished = FinishCallback()
        settings = {
            "kind": "left", "interval": 0.001, "count": 10, "key": "A",
            "position": False, "x": 0, "y": 0,
        }

        started = time.perf_counter()
        self.assertTrue(clicker.start(settings, finished))
        self.assertEqual(finished.result(), (True, "连点完成"))
        per_click = (time.perf_counter() - started) / 10
        # 系统计时器粒度是 15.625ms；退回 Event.wait 超时的话这里会跳到 0.015 左右。
        self.assertLess(per_click, 0.01)


class MacroRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name))
        self.sender = FakeSender()
        self.runner = MacroRunner(self.store, sender=self.sender)
        self.addCleanup(self.runner.stop)
        self.finished = FinishCallback()

    def test_recording_step_replays_every_event_and_repeat(self):
        events = [
            {"type": "mouse_down", "dt": 0, "button": "left"},
            {"type": "mouse_up", "dt": 0, "button": "left"},
            {"type": "wheel", "dt": 0, "delta": 240},
            {"type": "wheel", "dt": 0, "delta": -120},
            {"type": "key_down", "dt": 0, "vk": 65},
            {"type": "key_up", "dt": 0, "vk": 65},
            {"type": "move", "dt": 0, "x": 4, "y": 5},
        ]
        self.store.save("recording", "sample", {"events": events})
        macro = {
            "name": "回放", "repeat": 2,
            "steps": [{"type": "recording", "name": "sample"}],
        }

        self.assertTrue(self.runner.start(macro, self.finished))

        self.assertEqual(self.finished.result(), (True, "宏「回放」完成，共执行 2 次"))
        cycle = [
            ("mouse_button", "left", True),
            ("mouse_button", "left", False),
            ("wheel", "up"),
            ("wheel", "up"),
            ("wheel", "down"),
            ("key", 65, True),
            ("key", 65, False),
            ("move", 4, 5),
        ]
        self.assertEqual(self.sender.calls, cycle * 2)

    def test_macro_executes_click_key_hotkey_text_and_other_steps(self):
        macro = {
            "name": "步骤", "repeat": 2,
            "steps": [
                {"type": "click", "button": "right"},
                {"type": "move", "x": -3, "y": 8},
                {"type": "wheel", "direction": "down"},
                {"type": "key", "key": "a"},
                {"type": "hotkey", "key": "LCTRL+LSHIFT+c"},
                {"type": "text", "text": "你好A"},
                {"type": "wait", "seconds": 0},
            ],
        }

        self.assertTrue(self.runner.start(macro, self.finished))

        self.assertEqual(self.finished.result(), (True, "宏「步骤」完成，共执行 2 次"))
        cycle = [
            ("mouse_button", "right", True),
            ("mouse_button", "right", False),
            ("move", -3, 8),
            ("wheel", "down"),
            ("key", ord("A"), True),
            ("key", ord("A"), False),
            ("key", 0xA2, True),
            ("key", 0xA0, True),
            ("key", ord("C"), True),
            ("key", ord("C"), False),
            ("key", 0xA0, False),
            ("key", 0xA2, False),
            ("text", "你好A"),
        ]
        self.assertEqual(self.sender.calls, cycle * 2)

    def test_unlimited_macro_stops_during_wait(self):
        macro = {
            "name": "停止", "repeat": 0,
            "steps": [
                {"type": "click", "button": "middle"},
                {"type": "wait", "seconds": 60},
            ],
        }

        self.assertTrue(self.runner.start(macro, self.finished))
        self.sender.wait_for_calls(2)
        self.runner.stop()

        self.assertEqual(self.finished.result(), (True, "宏已停止"))
        self.assertEqual(
            self.sender.calls,
            [("mouse_button", "middle", True), ("mouse_button", "middle", False)],
        )
        self.assertFalse(self.runner.running)


if __name__ == "__main__":
    unittest.main()
