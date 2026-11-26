#!/usr/bin/env python3
"""打包发布脚本：同时产出目录版（portable.zip）与单文件版（standalone.exe）。

用法：
  py -3 scripts/build.py                 # 默认同时构建目录版和单文件版
  py -3 scripts/build.py --mode onedir   # 仅构建目录版（启动快、误报少）
  py -3 scripts/build.py --mode onefile  # 仅构建单文件版（免解压即用）
  py -3 scripts/build.py --check-tag v1.0.0   # 校验 tag 与 __version__ 一致（CI 用）
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ASSETS = ROOT / "aglauncher" / "assets"

APP_NAME = "AntigravityLauncher"
CLI_NAME = "aglaunch"

# 避免在包含大量数据分析/AI 包的全局环境里被 PyInstaller 误卷入无关模块
EXCLUDED_MODULES = (
    "matplotlib", "numpy", "scipy", "pandas", "PIL", "cv2", "torch",
    "IPython", "notebook", "jupyter", "pytest", "playwright", "cryptography",
    "aiohttp", "httpx", "requests", "urllib3", "pydantic", "sqlalchemy",
)


def read_version() -> str:
    init_py = (ROOT / "aglauncher" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init_py)
    return m.group(1) if m else "1.0.0"


def platform_tag() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if "arm" in machine or "aarch64" in machine else "x64"
    if sys.platform.startswith("win"):
        return f"win-{arch}"
    if sys.platform == "darwin":
        return f"mac-{arch}"
    return f"linux-{arch}"


def write_win_version_file(path: Path, version: str, internal_name: str, is_cli: bool = False) -> None:
    nums = [int(x) for x in (version.split(".") + ["0", "0", "0", "0"])[:4] if x.isdigit()]
    while len(nums) < 4:
        nums.append(0)
    ver_tuple = tuple(nums)
    desc = (
        "Antigravity Launcher CLI (Process-level proxy injector)"
        if is_cli
        else "Process-level proxy launcher for Google Antigravity"
    )
    content = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={ver_tuple},
    prodvers={ver_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'Antigravity Launcher Contributors'),
          StringStruct('FileDescription', '{desc}'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', '{internal_name}'),
          StringStruct('LegalCopyright', 'MIT License'),
          StringStruct('OriginalFilename', '{internal_name}.exe'),
          StringStruct('ProductName', 'Antigravity Launcher'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_pyinstaller(
    *,
    name: str,
    mode_flag: str,
    windowed: bool,
    work_dir: Path,
    dist_dir: Path,
    version_file: Path | None,
) -> None:
    sep = ";" if os.name == "nt" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        mode_flag,
        "--name", name,
        "--distpath", str(dist_dir),
        "--workpath", str(work_dir),
        "--specpath", str(work_dir),
    ]
    if windowed:
        cmd.append("--windowed")
    else:
        cmd.append("--console")

    ico = ASSETS / "icon.ico"
    png = ASSETS / "icon.png"
    if sys.platform.startswith("win") and ico.is_file():
        cmd.extend(["--icon", str(ico)])
        cmd.extend(["--add-data", f"{ico}{sep}assets"])
    if png.is_file():
        cmd.extend(["--add-data", f"{png}{sep}assets"])
    if version_file and sys.platform.startswith("win"):
        cmd.extend(["--version-file", str(version_file)])

    for mod in EXCLUDED_MODULES:
        cmd.extend(["--exclude-module", mod])

    cmd.append(str(ROOT / "aglaunch.py"))
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def zip_directory(src_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file in sorted(src_dir.rglob("*")):
            if file.is_file():
                arcname = Path(src_dir.name) / file.relative_to(src_dir)
                zf.write(file, arcname)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_onedir(version: str, tag: str) -> Path:
    stage = BUILD / "onedir_dist"
    shutil.rmtree(stage, ignore_errors=True)
    ver_gui = BUILD / "version_gui.txt"
    ver_cli = BUILD / "version_cli.txt"
    if sys.platform.startswith("win"):
        write_win_version_file(ver_gui, version, APP_NAME, is_cli=False)
        write_win_version_file(ver_cli, version, CLI_NAME, is_cli=True)

    # 1) 无黑框 GUI 主程序
    run_pyinstaller(
        name=APP_NAME,
        mode_flag="--onedir",
        windowed=True,
        work_dir=BUILD / "work_onedir_gui",
        dist_dir=stage,
        version_file=ver_gui if sys.platform.startswith("win") else None,
    )

    bundle_dir = stage / APP_NAME
    # 2) 同目录下额外放一个原生控制台入口 aglaunch.exe，共享同一份 _internal 运行时（仅增加几 MB 引导壳）
    cli_stage = BUILD / "onedir_cli_dist"
    shutil.rmtree(cli_stage, ignore_errors=True)
    run_pyinstaller(
        name=CLI_NAME,
        mode_flag="--onedir",
        windowed=False,
        work_dir=BUILD / "work_onedir_cli",
        dist_dir=cli_stage,
        version_file=ver_cli if sys.platform.startswith("win") else None,
    )
    cli_bin = cli_stage / CLI_NAME / (f"{CLI_NAME}.exe" if os.name == "nt" else CLI_NAME)
    if cli_bin.is_file():
        shutil.copy2(cli_bin, bundle_dir / cli_bin.name)

    for doc in ("README.md", "README_EN.md", "CHANGELOG.md", "LICENSE"):
        src = ROOT / doc
        if src.is_file():
            shutil.copy2(src, bundle_dir / doc)

    DIST.mkdir(parents=True, exist_ok=True)
    archive = DIST / f"{APP_NAME}-{version}-{tag}-portable.zip"
    zip_directory(bundle_dir, archive)
    return archive


def build_onefile(version: str, tag: str) -> Path:
    stage = BUILD / "onefile_dist"
    shutil.rmtree(stage, ignore_errors=True)
    ver_gui = BUILD / "version_onefile.txt"
    if sys.platform.startswith("win"):
        write_win_version_file(ver_gui, version, APP_NAME, is_cli=False)

    run_pyinstaller(
        name=APP_NAME,
        mode_flag="--onefile",
        windowed=True,
        work_dir=BUILD / "work_onefile",
        dist_dir=stage,
        version_file=ver_gui if sys.platform.startswith("win") else None,
    )

    ext = ".exe" if os.name == "nt" else ""
    built = stage / f"{APP_NAME}{ext}"
    DIST.mkdir(parents=True, exist_ok=True)
    target = DIST / f"{APP_NAME}-{version}-{tag}-standalone{ext}"
    shutil.copy2(built, target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Antigravity Launcher release artifacts.")
    parser.add_argument(
        "--mode",
        choices=("all", "onedir", "onefile"),
        default="all",
        help="Build mode: all (default), onedir (.zip), or onefile (.exe)",
    )
    parser.add_argument(
        "--check-tag",
        metavar="TAG",
        help="只校验 tag（如 v1.0.0）与 aglauncher.__version__ 是否一致，不执行打包",
    )
    args = parser.parse_args(argv)

    if args.check_tag:
        version = read_version()
        tag = args.check_tag.strip()
        if tag != f"v{version}":
            print(
                f"tag {tag} 与 aglauncher/__init__.py 的 __version__={version} 不一致，"
                "请先同步版本号再打 tag",
                file=sys.stderr,
            )
            return 1
        print(f"tag {tag} 与 __version__={version} 一致")
        return 0

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run: py -3 -m pip install pyinstaller", file=sys.stderr)
        return 1

    version = read_version()
    tag = platform_tag()
    artifacts: list[Path] = []

    if args.mode in ("all", "onedir"):
        artifacts.append(build_onedir(version, tag))
    if args.mode in ("all", "onefile"):
        artifacts.append(build_onefile(version, tag))

    sums_file = DIST / "SHA256SUMS.txt"
    lines = [f"{sha256_file(p)}  {p.name}" for p in artifacts]
    sums_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\nBuilt release artifacts:")
    for line in lines:
        print(" ", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
