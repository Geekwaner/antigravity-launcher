#!/usr/bin/env python3
"""入口脚本。不带参数直接开图形界面，带 --doctor / --launch 等走命令行。"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def _ensure_cli_console() -> None:
    """Windows 无控制台打包（--windowed）下执行 CLI 参数时，附加到父终端或安全兜底。"""
    if len(sys.argv) <= 1 or os.name != "nt":
        return
    if sys.stdout is not None and sys.stderr is not None:
        return

    import ctypes

    attached = False
    try:
        attached = bool(ctypes.WinDLL("kernel32").AttachConsole(-1))
    except OSError:
        attached = False

    if attached:
        if sys.stdout is None:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
        if sys.stderr is None:
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
        if sys.stdin is None:
            sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")  # noqa: SIM115
    else:
        if sys.stdout is None:
            sys.stdout = io.TextIOWrapper(open(os.devnull, "wb"), encoding="utf-8")  # noqa: SIM115
        if sys.stderr is None:
            sys.stderr = io.TextIOWrapper(open(os.devnull, "wb"), encoding="utf-8")  # noqa: SIM115


_ensure_cli_console()

from aglauncher.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
