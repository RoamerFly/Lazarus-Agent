"""Small replacement helper launched by the signed-update flow on Windows."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import time


def wait_for_parent(parent_pid: int, *, attempts: int = 240) -> None:
    """Do not touch the running EXE until the desktop host actually exits."""
    # A normal Edge app close reaches this point within seconds. Two minutes is
    # deliberately finite: silently replacing while the app still runs is worse
    # than leaving the signed download available for a retry.
    for _ in range(attempts):
        try:
            os.kill(parent_pid, 0)
        except OSError:
            return
        time.sleep(0.5)
    raise RuntimeError("工作台仍在运行，未执行更新；请关闭后重新安装。")


def apply_portable_update(source: Path, target: Path, parent_pid: int) -> None:
    source, target = source.resolve(), target.resolve()
    if not source.is_file() or source.suffix.lower() != ".exe" or target.suffix.lower() != ".exe":
        raise ValueError("更新文件必须是已验证的 EXE。")
    wait_for_parent(parent_pid)
    replacement = target.with_suffix(".new.exe")
    shutil.copy2(source, replacement)
    for _ in range(30):
        try:
            os.replace(replacement, target)
            break
        except PermissionError:
            time.sleep(1)
    else:
        raise RuntimeError("应用仍在运行，无法完成替换。")
    subprocess.Popen([str(target)], close_fds=True)


def apply_installer_update(source: Path, target: Path, parent_pid: int) -> None:
    source, target = source.resolve(), target.resolve()
    if not source.is_file() or source.suffix.lower() != ".exe" or target.suffix.lower() != ".exe":
        raise ValueError("安装程序或目标程序无效。")
    wait_for_parent(parent_pid)
    # NSIS `/S` is non-interactive and installs only for the current user. It
    # preserves user data because the installer never owns %LOCALAPPDATA%\Lazarus-Agent.
    completed = subprocess.run([str(source), "/S"], check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"安装程序退出码为 {completed.returncode}，未重新启动。")
    if not target.is_file():
        raise RuntimeError("安装完成后未找到桌面程序，未重新启动。")
    subprocess.Popen([str(target)], close_fds=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--mode", choices=("portable", "installer"), default="portable")
    args = parser.parse_args()
    operation = apply_installer_update if args.mode == "installer" else apply_portable_update
    operation(Path(args.source), Path(args.target), args.parent_pid)


if __name__ == "__main__":
    main()
