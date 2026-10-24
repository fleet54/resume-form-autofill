"""检查运行环境，缺什么直接补什么。

双击「检查环境.cmd」就会跑到这个脚本；也可以自己运行：
    python _env_check.py
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
lines: list[str] = []
ok = True


def say(text: str = "") -> None:
    print(text)
    lines.append(text)


def check_python() -> None:
    v = sys.version_info
    say(f"[1] Python  : {v.major}.{v.minor}.{v.micro}  ({platform.architecture()[0]})")
    say(f"    解释器  : {sys.executable}")
    if v < (3, 9):
        say("    ✗ 版本太低，需要 3.9 或更高：https://www.python.org/downloads/windows/")
        globals()["ok"] = False
    else:
        say("    ✓ 版本没问题")


def check_tkinter() -> None:
    try:
        import tkinter  # noqa: F401
        import tkinter.ttk  # noqa: F401

        say("[2] tkinter : ✓ 可用（软件界面靠它）")
    except Exception as exc:  # pragma: no cover
        say(f"[2] tkinter : ✗ 不可用（{exc}）")
        say("    重装 Python 时勾选 「tcl/tk and IDLE」 即可")
        globals()["ok"] = False


def check_file(name: str) -> None:
    p = HERE / name
    if p.exists():
        say(f"    ✓ {name}  ({p.stat().st_size:,} 字节)")
    else:
        say(f"    ✗ 缺少 {name} —— 这个文件夹不完整，请重新解压整包")
        globals()["ok"] = False


def check_websockets() -> None:
    try:
        import websockets  # noqa: F401

        say("[4] websockets : ✓ 已安装（可以让软件自己开浏览器）")
        return
    except Exception:
        say("[4] websockets : ✗ 没装")
    say("    影响：只有「让软件自己开浏览器」用不了；『复制脚本』那条路依然能用。")
    answer = input("    现在自动安装吗？(Y/n) ").strip().lower()
    if answer in ("", "y", "yes", "是"):
        cmds = [[sys.executable, "-m", "pip", "install", "--user", "websockets"]]
        mirrors = ["https://pypi.tuna.tsinghua.edu.cn/simple"]
        for base in cmds:
            for m in [None] + mirrors:
                cmd = base + (["-i", m] if m else [])
                say(f"    → {' '.join(cmd)}")
                r = subprocess.run(cmd)
                if r.returncode == 0:
                    say("    ✓ 装好了")
                    return
        say("    ✗ 自动安装失败。可以手动在命令行里跑：")
        say("      python -m pip install --user websockets -i https://pypi.tuna.tsinghua.edu.cn/simple")
    else:
        say("    跳过安装。")


def check_browser() -> None:
    found = []
    for name, paths in {
        "Chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ],
        "Edge": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
    }.items():
        for p in paths:
            if Path(p).exists():
                found.append(f"{name} ({p})")
                break
    if found:
        say("[5] 浏览器  : ✓ " + " / ".join(found))
    else:
        say("[5] 浏览器  : ✗ 没找到 Chrome 或 Edge")
        say("    装一个即可：https://www.google.com/chrome/ 或系统自带的 Edge")


def check_clipboard() -> None:
    say(f"[6] 剪贴板  : {'✓ 有（复制脚本时会用到）' if shutil.which('clip') or os.name == 'nt' else '?'}")


def main() -> int:
    say("=" * 56)
    say(" 网申简历填写助手 · 环境检查")
    say("=" * 56)
    say("")
    check_python()
    check_tkinter()
    say("[3] 关键文件：")
    for f in ("gui.py", "filler.py", "filler.js", "rules.json", "profile.json", "make_snippet.py"):
        check_file(f)
    check_websockets()
    check_browser()
    check_clipboard()
    say("")
    if ok:
        say("结论：环境没问题，双击「启动助手.cmd」就能用。")
    else:
        say("结论：上面带 ✗ 的项需要处理，处理完再试一次。")
    say("")
    try:
        (HERE / "env-report.txt").write_text("\n".join(lines), encoding="utf-8")
        print("（这份报告也存到了 env-report.txt，出问题时可以把它发给别人看）")
    except Exception:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
