"""定位 Antigravity 客户端。

只认"旁边确实躺着 resources/bin/language_server"的可执行文件，
避免把同名的其他程序（比如某个叫 Antigravity 的小工具）当成目标。
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_WRAPPER_RE = re.compile(r"(launcher|proxy)", re.IGNORECASE)


@dataclass(frozen=True)
class Client:
    exe: Path
    language_server: Path
    root: Path
    source: str

    @property
    def display_root(self) -> str:
        return str(self.root)


@dataclass(frozen=True)
class CliTool:
    exe: Path
    source: str


def _ls_filename() -> str:
    return "language_server.exe" if os.name == "nt" else "language_server"


def language_server_for(exe: Path) -> Path | None:
    """客户端根目录下必须存在 resources/bin/language_server，才算认对了。"""
    ls_name = _ls_filename()
    candidates = [
        exe.parent / "resources" / "bin" / ls_name,
        # macOS .app: Contents/MacOS/<exec> -> Contents/Resources/bin/language_server
        exe.parent.parent / "Resources" / "bin" / ls_name,
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def _win_candidates() -> list[Path]:
    out: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        programs = Path(local) / "Programs"
        if programs.is_dir():
            try:
                for child in sorted(programs.iterdir()):
                    if child.is_dir() and "antigravity" in child.name.lower():
                        out.append(child / "Antigravity.exe")
            except OSError:
                pass
        # 兜底：目录名被改过也能命中
        out.append(programs / "antigravity" / "Antigravity.exe")
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432"):
        base = os.environ.get(env)
        if not base:
            continue
        for name in ("Antigravity", "antigravity"):
            out.append(Path(base) / name / "Antigravity.exe")
    return out


def _mac_bundle_exec(app: Path) -> Path | None:
    exe_name = "Antigravity"
    plist = app / "Contents" / "Info.plist"
    try:
        import plistlib

        with plist.open("rb") as fh:
            meta = plistlib.load(fh)
        exe_name = meta.get("CFBundleExecutable") or exe_name
    except Exception:
        pass
    cand = app / "Contents" / "MacOS" / exe_name
    return cand if cand.is_file() else None


def _mac_candidates() -> list[Path]:
    roots = [Path("/Applications"), Path.home() / "Applications"]
    out: list[Path] = []
    for r in roots:
        if not r.is_dir():
            continue
        try:
            entries = sorted(r.iterdir())
        except OSError:
            continue
        for app in entries:
            if not app.is_dir() or app.suffix.lower() != ".app":
                continue
            if "antigrav" not in app.name.lower() or _WRAPPER_RE.search(app.name):
                continue
            exec_path = _mac_bundle_exec(app)
            if exec_path:
                out.append(exec_path)
    try:
        res = subprocess.run(
            ["mdfind", 'kMDItemFSName == "Antigravity.app"'],
            capture_output=True, text=True, timeout=4,
        )
        for line in res.stdout.splitlines():
            app = Path(line.strip())
            if app.is_dir() and not _WRAPPER_RE.search(app.name):
                exec_path = _mac_bundle_exec(app)
                if exec_path:
                    out.append(exec_path)
    except Exception:  # noqa: BLE001
        pass
    return out


def _linux_candidates() -> list[Path]:
    out = [
        Path("/opt/antigravity/antigravity"),
        Path("/opt/Antigravity/antigravity"),
        Path("/usr/share/antigravity/antigravity"),
        Path("/usr/bin/antigravity"),
        Path("/usr/local/bin/antigravity"),
        Path.home() / ".local" / "share" / "antigravity" / "antigravity",
        Path.home() / ".local" / "bin" / "antigravity",
    ]
    for d in (Path.home() / ".local" / "share" / "applications", Path("/usr/share/applications")):
        if not d.is_dir():
            continue
        try:
            files = sorted(d.iterdir())
        except OSError:
            continue
        for f in files:
            if "antigrav" not in f.name.lower() or f.suffix != ".desktop" or _WRAPPER_RE.search(f.name):
                continue
            try:
                for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith("Exec="):
                        tokens = shlex.split(line[5:].strip())
                        if tokens:
                            out.append(Path(tokens[0]))
                        break
            except OSError:
                pass
    return out


def candidates() -> list[Path]:
    if sys.platform.startswith("win"):
        raw = _win_candidates()
    elif sys.platform == "darwin":
        raw = _mac_candidates()
    else:
        raw = _linux_candidates()

    seen: set[str] = set()
    uniq: list[Path] = []
    for p in raw:
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def locate(saved: str | None = None) -> tuple[Client | None, list[str]]:
    """返回 (客户端, 探测记录)。探测记录用于在界面上解释"为什么没找到"。"""
    notes: list[str] = []

    if saved:
        exe = Path(saved)
        if exe.is_file():
            ls = language_server_for(exe)
            if ls:
                return Client(exe=exe, language_server=ls, root=exe.parent, source="记忆路径"), notes
            notes.append(f"记忆路径缺少 language_server：{exe}")
        else:
            notes.append(f"记忆路径已失效：{exe}")

    for exe in candidates():
        if not exe.is_file():
            continue
        ls = language_server_for(exe)
        if ls:
            return Client(exe=exe, language_server=ls, root=exe.parent, source="自动扫描"), notes
        notes.append(f"命中同名程序但非目标（无 language_server）：{exe}")

    return None, notes


def _cli_filename() -> str:
    return "agy.exe" if sys.platform.startswith("win") else "agy"


def _cli_candidates() -> list[Path]:
    name = _cli_filename()
    out: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "agy" / "bin" / name)
    home = Path.home()
    out.append(home / ".agy" / "bin" / name)
    out.append(home / ".local" / "bin" / name)
    if not sys.platform.startswith("win"):
        out.append(Path("/usr/local/bin") / name)
    return out


def locate_cli(saved: str | None = None) -> tuple[CliTool | None, list[str]]:
    """定位 Antigravity CLI（agy / agy.exe）。"""
    notes: list[str] = []
    if saved:
        exe = Path(saved)
        if exe.is_file():
            return CliTool(exe=exe, source="记忆路径"), notes
        notes.append(f"记忆的 CLI 路径已失效：{exe}")

    for cand in _cli_candidates():
        if cand.is_file():
            return CliTool(exe=cand, source="自动扫描"), notes

    which_hit = shutil.which(_cli_filename())
    if which_hit:
        exe = Path(which_hit)
        if exe.is_file():
            return CliTool(exe=exe, source="PATH"), notes

    return None, notes
