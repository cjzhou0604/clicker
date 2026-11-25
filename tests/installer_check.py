"""安装包自检（需要本机装过 Inno Setup，手动运行：python tests/installer_check.py）。

流程：编译 build\\installer.iss -> 静默安装到 build\\selftest\\app -> 核对文件 ->
对新装出来的 Clicker.exe 跑 exe_check -> 静默卸载 -> 确认 data\\ 被保留。

为了不污染系统，测试用的脚本副本会关掉注册表卸载项，并去掉快捷方式和 [Run]：
写 HKCU\\...\\Uninstall、创建 .lnk 在受限环境里会被拒绝（错误 5 / 0x80070005），
而这两条只在交互安装时才需要人工确认。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "build" / "selftest"
ISCC_CANDIDATES = [
    r"E:\Program Files\Inno Setup 7\ISCC.exe",
    r"C:\Program Files\Inno Setup 7\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def report() -> int:
    failed = [name for name, ok, _ in results if not ok]
    print(f"\n共 {len(results)} 项，通过 {len(results) - len(failed)} 项"
          + (f"，失败：{failed}" if failed else "，全部通过"))
    return 1 if failed else 0


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kw)


def purge(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.is_file():
        path.unlink(missing_ok=True)


def find_iscc() -> Path | None:
    if len(sys.argv) > 1:
        override = Path(sys.argv[1])
        return override if override.is_file() else None
    for candidate in ISCC_CANDIDATES:
        if Path(candidate).is_file():
            return Path(candidate)
    return None


def main() -> int:
    iscc = find_iscc()
    if iscc is None:
        raise SystemExit("找不到 ISCC.exe，参数里传入它的路径，或安装 Inno Setup 7。")
    exe = ROOT / "Clicker.exe"
    internal = ROOT / "_internal"
    if not (exe.is_file() and internal.is_dir()):
        raise SystemExit("先运行 build\\rebuild.bat 生成 Clicker.exe 和 _internal，再打包安装包。")

    text = (ROOT / "build" / "installer.iss").read_text(encoding="utf-8-sig")
    text = text.replace("[Setup]\n", "[Setup]\nCreateUninstallRegKey=no\n")
    text = text.replace("OutputDir=installer", "OutputDir=selftest")
    text = text.replace("{#MyAppNameEn}-Setup-{#MyAppVersion}", "installer-check")
    for section in ("Run", "Icons", "Tasks"):
        text = re.sub(rf"\n\[{section}\]\n(?:.*\n)*?\n", "\n", text)
    check("测试脚本副本去掉注册表与快捷方式", "CreateUninstallRegKey=no" in text and "[Icons]" not in text)

    purge(WORK)
    WORK.mkdir(parents=True, exist_ok=True)
    # .iss 里用 ..\ 引用打包产物，所以脚本必须留在 build\ 下，产物才指得到根目录。
    iss = ROOT / "build" / "_installer_check.iss"
    iss.write_text(text, encoding="utf-8-sig")
    compiled = run([str(iscc), "/Q", str(iss)])
    iss.unlink(missing_ok=True)
    setup = WORK / "installer-check.exe"
    check("安装包编译成功", compiled.returncode == 0 and setup.is_file(), f"退出码 {compiled.returncode}")
    if not setup.is_file():
        print((compiled.stdout or "")[-800:])
        return report()

    target = WORK / "app"
    log = WORK / "install.log"
    installed = run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                     f"/LOG={log}", f"/DIR={target}"])
    check("静默安装成功", installed.returncode == 0, f"退出码 {installed.returncode}")
    if not target.is_dir():
        return report()

    copied = len(list((target / "_internal").rglob("*"))) if (target / "_internal").is_dir() else 0
    check("Clicker.exe 就位", (target / "Clicker.exe").is_file())
    check("_internal 运行时完整", copied > 900, f"{copied} 项")
    check("说明文档随包安装", (target / "README.md").is_file() and (target / "USER_GUIDE.md").is_file())
    check("data 不会被打包进安装程序", not (target / "data").exists())

    data = target / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "config.json").write_text(json.dumps({"hotkeys": {"primary": "F8"}}, ensure_ascii=False),
                                      encoding="utf-8")
    (data / "recordings.json").write_text(
        json.dumps([{"name": "自检录制", "hotkey": "", "steps": []}], ensure_ascii=False),
        encoding="utf-8")

    checked = run([sys.executable, str(ROOT / "tests" / "exe_check.py"),
                   str(target / "Clicker.exe")], cwd=str(ROOT), timeout=300)
    lines = (checked.stdout or checked.stderr or "").strip().splitlines()
    check("安装后的 exe 通过自检", checked.returncode == 0, lines[-1] if lines else f"退出码 {checked.returncode}")

    uninstaller = target / "unins000.exe"
    check("卸载程序已生成", uninstaller.is_file())
    if uninstaller.is_file():
        removed = run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES"])
        check("静默卸载成功", removed.returncode == 0, f"退出码 {removed.returncode}")
        deadline = time.monotonic() + 20
        while (target / "Clicker.exe").exists() and time.monotonic() < deadline:
            time.sleep(0.5)
        left = sorted(p.name for p in target.iterdir()) if target.exists() else ["目录已删除"]
        check("卸载后程序文件被清除", not (target / "Clicker.exe").exists()
              and not (target / "_internal").exists(), str(left))
        check("卸载后 data 保留", (data / "config.json").is_file()
              and (data / "recordings.json").is_file())

    reg = run(["reg.exe", "query",
               r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall"
               r"\{29C25F91-AE90-43D8-B3F4-CB1CD844910F}_is1"])
    check("没留下残留卸载项", reg.returncode != 0)

    print("提示：开始菜单快捷方式、UAC 提权安装、装到 Program Files 后的目录权限，"
          "需要双击安装包在交互模式下人工确认。")
    for path in (setup, log):
        purge(path)
    purge(target)
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
