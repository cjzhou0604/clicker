"""原子保存配置、录制和宏，所有文件均位于程序目录下的 data。"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path

if getattr(sys, "frozen", False):
    # 打包后 __file__ 指向临时解压目录，数据必须落在 exe 所在的文件夹。
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RECORDING_DIR = DATA_DIR / "recordings"
MACRO_DIR = DATA_DIR / "macros"
CONFIG_FILE = DATA_DIR / "config.json"
SCHEMA_VERSION = 1
DEFAULT_CONFIG = {
    "version": SCHEMA_VERSION,
    "hotkey": "F8",
    "stop_hotkey": "F9",
    "playback_hotkey": "F7",
    "click": {
        "kind": "left",
        "interval": 0.1,
        "key": "A",
        "count": 0,
        "position": False,
        "x": 0,
        "y": 0,
    },
    "playback": {
        "minimize": False,
        "delay": 0.0,
    },
    "last_recording": "",
}


class Store:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or DATA_DIR
        self.recording_dir = self.data_dir / "recordings"
        self.macro_dir = self.data_dir / "macros"
        self.config_file = self.data_dir / "config.json"
        self._lock = threading.RLock()
        for directory in (self.data_dir, self.recording_dir, self.macro_dir):
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _name(value: str) -> str:
        value = value.strip()
        if not value or value in (".", "..") or len(value) > 60:
            raise ValueError("名称须为 1–60 个字符")
        if re.search(r'[<>:"/\\|?*\x00-\x1f]', value):
            raise ValueError('名称不能包含 < > : " / \\ | ? * 或控制字符')
        return value

    def _path(self, kind: str, name: str) -> Path:
        folder = self.recording_dir if kind == "recording" else self.macro_dir
        return folder / (self._name(name) + ".json")

    @staticmethod
    def _write_json(path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    @staticmethod
    def _read_json(path: Path):
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def load_config(self) -> dict:
        result = json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            saved = self._read_json(self.config_file)
            if isinstance(saved, dict):
                for key in ("hotkey", "stop_hotkey", "playback_hotkey", "last_recording"):
                    if isinstance(saved.get(key), str):
                        result[key] = saved[key]
                if isinstance(saved.get("click"), dict):
                    result["click"].update(saved["click"])
                if isinstance(saved.get("playback"), dict):
                    result["playback"].update(saved["playback"])
        except (OSError, ValueError):
            pass
        self._validate_config(result)
        return result

    @staticmethod
    def _validate_config(config: dict) -> None:
        click = config["click"]
        if click.get("kind") not in ("left", "right", "middle", "wheel_up", "wheel_down", "key"):
            raise ValueError("无效的点击类型")
        if not isinstance(click.get("interval"), (int, float)) or not 0.001 <= click["interval"] <= 3600:
            raise ValueError("点击间隔须在 0.001–3600 秒之间")
        if not isinstance(click.get("count"), int) or not 0 <= click["count"] <= 10000000:
            raise ValueError("点击次数须为 0–10000000，0 表示不限")
        if not isinstance(click.get("key"), str):
            raise ValueError("键盘按键无效")
        if not isinstance(click.get("position"), bool):
            raise ValueError("固定位置设置无效")
        if not all(isinstance(click.get(p), int) and -100000 <= click[p] <= 100000 for p in ("x", "y")):
            raise ValueError("固定坐标无效")
        playback = config.get("playback")
        if not isinstance(playback, dict):
            raise ValueError("播放设置无效")
        if not isinstance(playback.get("minimize"), bool):
            raise ValueError("播放最小化设置无效")
        delay = playback.get("delay")
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 0 <= delay <= 3600:
            raise ValueError("播放延时须在 0–3600 秒之间")

    def save_config(self, config: dict) -> None:
        self._validate_config(config)
        with self._lock:
            self._write_json(self.config_file, config)

    def list_names(self, kind: str) -> list[str]:
        folder = self.recording_dir if kind == "recording" else self.macro_dir
        return sorted((p.stem for p in folder.glob("*.json")), key=str.casefold)

    def save(self, kind: str, name: str, payload: dict) -> dict:
        if kind == "recording":
            events = payload.get("events")
            if not isinstance(events, list) or len(events) > 1000000:
                raise ValueError("录制内容无效或事件过多")
            for event in events:
                validate_event(event)
            document = {
                "version": SCHEMA_VERSION, "type": "recording",
                "name": self._name(name), "hotkey": payload.get("hotkey", ""),
                "events": events,
            }
        elif kind == "macro":
            steps = payload.get("steps")
            if not isinstance(steps, list) or not steps or len(steps) > 1000:
                raise ValueError("宏须包含 1–1000 个步骤")
            for step in steps:
                validate_step(step)
            repeat = payload.get("repeat", 1)
            if not isinstance(repeat, int) or not 0 <= repeat <= 100000:
                raise ValueError("宏循环次数须为 0–100000，0 表示不限")
            document = {
                "version": SCHEMA_VERSION, "type": "macro", "name": self._name(name),
                "hotkey": payload.get("hotkey", ""), "repeat": repeat, "steps": steps,
            }
        else:
            raise ValueError("未知文档类型")
        with self._lock:
            self._write_json(self._path(kind, name), document)
        return document

    def load(self, kind: str, name: str) -> dict:
        path = self._path(kind, name)
        try:
            document = self._read_json(path)
        except FileNotFoundError:
            raise ValueError(f"找不到{'录制' if kind == 'recording' else '宏'}：{name}")
        except (OSError, ValueError) as exc:
            raise ValueError(f"无法读取 {name}：{exc}")
        if not isinstance(document, dict) or document.get("type") != kind:
            raise ValueError(f"{name} 的文件格式不正确")
        # 读取时校验，但绝不改写原文件。
        if kind == "recording":
            events = document.get("events")
            if not isinstance(events, list) or len(events) > 1000000:
                raise ValueError("录制内容无效或事件过多")
            for event in events:
                validate_event(event)
        else:
            steps = document.get("steps")
            if not isinstance(steps, list) or not steps or len(steps) > 1000:
                raise ValueError("宏须包含 1–1000 个步骤")
            for step in steps:
                validate_step(step)
            repeat = document.get("repeat", 1)
            if not isinstance(repeat, int) or not 0 <= repeat <= 100000:
                raise ValueError("宏循环次数无效")
        return document

    def delete(self, kind: str, name: str) -> None:
        try:
            self._path(kind, name).unlink()
        except FileNotFoundError:
            pass


def validate_event(event) -> None:
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise ValueError("事件格式无效")
    kind = event["type"]
    if not isinstance(event.get("dt"), (int, float)) or not 0 <= event["dt"] <= 86400:
        raise ValueError("事件延迟无效")
    if kind in ("mouse_down", "mouse_up"):
        if event.get("button") not in ("left", "right", "middle"):
            raise ValueError("鼠标按键无效")
    elif kind == "wheel":
        if not isinstance(event.get("delta"), int) or event["delta"] == 0 or abs(event["delta"]) > 10000:
            raise ValueError("滚轮事件无效")
        if abs(event["delta"]) > 120 and event["delta"] % 120:
            raise ValueError("滚轮事件无效")
    elif kind in ("key_down", "key_up"):
        if not isinstance(event.get("vk"), int) or not 1 <= event["vk"] <= 254:
            raise ValueError("键盘事件无效")
    elif kind == "move":
        if not all(isinstance(event.get(p), int) for p in ("x", "y")):
            raise ValueError("鼠标坐标无效")
    else:
        raise ValueError("未知事件类型")


def validate_step(step) -> None:
    if not isinstance(step, dict):
        raise ValueError("宏步骤格式无效")
    kind = step.get("type")
    if kind == "recording":
        Store._name(step.get("name", ""))
    elif kind in ("click", "key", "text", "hotkey"):
        if kind == "click" and step.get("button") not in ("left", "right", "middle"):
            raise ValueError("宏鼠标按键无效")
        if kind in ("key", "hotkey") and (not isinstance(step.get("key"), str) or not step["key"]):
            raise ValueError("宏按键无效")
        if kind == "text" and (not isinstance(step.get("text"), str) or len(step["text"]) > 10000):
            raise ValueError("宏文本无效")
    elif kind == "move":
        if not all(isinstance(step.get(p), int) for p in ("x", "y")):
            raise ValueError("宏坐标无效")
    elif kind == "wheel":
        if step.get("direction") not in ("up", "down"):
            raise ValueError("宏滚轮方向无效")
    elif kind == "wait":
        if not isinstance(step.get("seconds"), (int, float)) or not 0 <= step["seconds"] <= 86400:
            raise ValueError("宏等待时间无效")
    else:
        raise ValueError("未知宏步骤类型")
