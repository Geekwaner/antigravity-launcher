"""本地偏好持久化。

只存"启动器自己"的偏好（客户端路径、手动指定的代理、附加参数），
不触碰 Antigravity 自身的任何配置，也不写注册表。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_DIR_NAME = "AntigravityLauncher"

DEFAULTS: dict[str, Any] = {
    "client_path": None,       # 手动指定的 Antigravity.exe
    "cli_path": None,          # 手动指定的 agy 可执行文件
    "proxy_url": None,         # 手动指定的代理，如 http://127.0.0.1:7890
    "work_dir": None,          # 打开终端/CLI 时的默认工作目录
    "extra_args": [],          # 透传给客户端的额外参数
    "verify_attempts": 2,      # 每个 (协议, 目标) 的重试次数
    "verify_timeout": 6.0,     # 单次握手超时（秒）
    "preflight_check": True,   # 启动前是否执行 Google 真实可达性校验
    "launched_desktop": None,  # 由本启动器拉起的桌面端记录 {pid, created, exe}
}


def config_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    d = root / APP_DIR_NAME
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> dict[str, Any]:
    data = dict(DEFAULTS)
    p = config_path()
    try:
        with p.open("r", encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            for k, v in stored.items():
                if k in DEFAULTS or k.startswith("_"):
                    data[k] = v
    except (OSError, ValueError):
        pass
    return data


def save(data: dict[str, Any]) -> None:
    payload = {k: v for k, v in data.items() if not k.startswith("_")}
    try:
        p = config_path()
        tmp = p.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except OSError:
        pass
