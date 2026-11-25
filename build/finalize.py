"""把 build/dist/Clicker 里的产物铺到项目根目录。

onedir 布局是「Clicker.exe + _internal/」，两者必须同级；这里只搬这两样，
data/ 和源码目录不受影响。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "build" / "dist" / "Clicker"


def main() -> int:
    if not SOURCE.is_dir():
        print(f"找不到打包输出：{SOURCE}")
        return 1
    exe = SOURCE / "Clicker.exe"
    internal = SOURCE / "_internal"
    if not exe.is_file() or not internal.is_dir():
        print("打包输出不完整，应同时包含 Clicker.exe 和 _internal")
        return 1

    target_internal = ROOT / "_internal"
    if target_internal.exists():
        shutil.rmtree(target_internal)      # 只清 this 程序生成的运行时目录
    shutil.copy2(exe, ROOT / exe.name)
    shutil.copytree(internal, target_internal)
    shutil.rmtree(SOURCE.parent)              # build/dist 只是中转，避免留一份重复产物
    files = sum(1 for _ in target_internal.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in [ROOT / exe.name, *target_internal.rglob("*")]
               if f.is_file()) / 1048576
    print(f"已就位：{exe.name} + _internal（{files} 个文件，共 {size:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
