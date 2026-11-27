#!/usr/bin/env python3
"""打包发布脚本：Windows 单文件版（standalone.exe）与 macOS Apple Silicon 安装镜像（.dmg）。

用法：
  python scripts/build.py                    # 构建当前平台发行包（Windows: .exe / macOS: .dmg）
  python scripts/build.py --check-tag v1.0.0 # 校验 tag 与 __version__ 一致（CI 用）
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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ASSETS = ROOT / "aglauncher" / "assets"

APP_NAME = "AntigravityLauncher"
DISPLAY_NAME = "Antigravity Launcher"
CLI_NAME = "aglaunch"
MAC_BUNDLE_ID = "io.github.geekwaner.antigravity-launcher"

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


def arch_name(machine: str | None = None) -> str:
    raw = (machine if machine is not None else platform.machine()).lower()
    if "arm" in raw or "aarch64" in raw:
        return "arm64"
    return "x64"


def platform_tag() -> str:
    arch = arch_name()
    if sys.platform.startswith("win"):
        return f"win-{arch}"
    if sys.platform == "darwin":
        return f"mac-{arch}"
    return f"linux-{arch}"


def write_win_version_file(path: Path, version: str, internal_name: str) -> None:
    nums = [int(x) for x in (version.split(".") + ["0", "0", "0", "0"])[:4] if x.isdigit()]
    while len(nums) < 4:
        nums.append(0)
    ver_tuple = tuple(nums)
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
          StringStruct('FileDescription', 'Process-level proxy launcher for Google Antigravity'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', '{internal_name}'),
          StringStruct('LegalCopyright', 'MIT License'),
          StringStruct('OriginalFilename', '{internal_name}.exe'),
          StringStruct('ProductName', '{DISPLAY_NAME}'),
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
    version_file: Path | None = None,
    icon_file: Path | None = None,
    bundle_id: str | None = None,
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
    if icon_file and icon_file.is_file():
        cmd.extend(["--icon", str(icon_file)])
    elif sys.platform.startswith("win") and ico.is_file():
        cmd.extend(["--icon", str(ico)])

    if sys.platform.startswith("win") and ico.is_file():
        cmd.extend(["--add-data", f"{ico}{sep}assets"])
    if png.is_file():
        cmd.extend(["--add-data", f"{png}{sep}assets"])
    if version_file and sys.platform.startswith("win"):
        cmd.extend(["--version-file", str(version_file)])
    if bundle_id and sys.platform == "darwin":
        cmd.extend(["--osx-bundle-identifier", bundle_id])

    for mod in EXCLUDED_MODULES:
        cmd.extend(["--exclude-module", mod])

    cmd.append(str(ROOT / "aglaunch.py"))
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Windows 单文件打包
# --------------------------------------------------------------------------- #

def build_onefile(version: str, tag: str) -> Path:
    """构建 Windows 单文件版（standalone.exe）。"""
    stage = BUILD / "onefile_dist"
    shutil.rmtree(stage, ignore_errors=True)
    ver_gui = BUILD / "version_onefile.txt"
    if sys.platform.startswith("win"):
        write_win_version_file(ver_gui, version, APP_NAME)

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


# --------------------------------------------------------------------------- #
# macOS Apple Silicon 打包（.app -> .dmg）
# --------------------------------------------------------------------------- #

def make_macos_icns(png_path: Path, out_icns: Path) -> Path | None:
    """使用 macOS 内置 sips + iconutil 从 icon.png 生成标准多尺寸 .icns。"""
    if not png_path.is_file():
        return None
    if not shutil.which("sips") or not shutil.which("iconutil"):
        return None

    iconset = BUILD / "icon.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True, exist_ok=True)
    specs = (
        ("icon_16x16.png", 16),
        ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32),
        ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512),
    )
    for filename, px in specs:
        subprocess.run(
            ["sips", "-z", str(px), str(px), str(png_path), "--out", str(iconset / filename)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    out_icns.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out_icns)], check=True)
    return out_icns if out_icns.is_file() else None


def build_macos_dmg(version: str, tag: str) -> Path:
    """构建 macOS .app 应用包并封装为标准拖拽安装 .dmg。"""
    stage = BUILD / "macos_dist"
    shutil.rmtree(stage, ignore_errors=True)
    icns = make_macos_icns(ASSETS / "icon.png", BUILD / "icon.icns")

    run_pyinstaller(
        name=APP_NAME,
        mode_flag="--onedir",
        windowed=True,
        work_dir=BUILD / "work_macos",
        dist_dir=stage,
        icon_file=icns,
        bundle_id=MAC_BUNDLE_ID,
    )

    app_bundle = stage / f"{APP_NAME}.app"
    if not app_bundle.is_dir():
        raise FileNotFoundError(f"Expected macOS app bundle not found: {app_bundle}")

    plist_path = app_bundle / "Contents" / "Info.plist"
    if plist_path.is_file():
        import plistlib

        with plist_path.open("rb") as fh:
            meta = plistlib.load(fh)
        meta["CFBundleShortVersionString"] = version
        meta["CFBundleVersion"] = version
        meta["CFBundleDisplayName"] = DISPLAY_NAME
        with plist_path.open("wb") as fh:
            plistlib.dump(meta, fh)

    macos_dir = app_bundle / "Contents" / "MacOS"
    cli_link = macos_dir / CLI_NAME
    if cli_link.exists() or cli_link.is_symlink():
        cli_link.unlink()
    os.symlink(APP_NAME, cli_link)

    if shutil.which("codesign"):
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)], check=True)

    dmg_stage = BUILD / "macos_dmg_stage"
    shutil.rmtree(dmg_stage, ignore_errors=True)
    dmg_stage.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ditto", str(app_bundle), str(dmg_stage / app_bundle.name)], check=True)
    os.symlink("/Applications", dmg_stage / "Applications")

    DIST.mkdir(parents=True, exist_ok=True)
    dmg_path = DIST / f"{APP_NAME}-{version}-{tag}.dmg"
    if dmg_path.exists():
        dmg_path.unlink()
    subprocess.run(
        [
            "hdiutil", "create",
            "-volname", DISPLAY_NAME,
            "-srcfolder", str(dmg_stage),
            "-ov",
            "-format", "UDZO",
            str(dmg_path),
        ],
        check=True,
    )
    return dmg_path


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Antigravity Launcher release artifacts.")
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
        print("PyInstaller is not installed. Run: python -m pip install pyinstaller", file=sys.stderr)
        return 1

    version = read_version()
    tag = platform_tag()

    if sys.platform.startswith("win"):
        artifact = build_onefile(version, tag)
    elif sys.platform == "darwin":
        artifact = build_macos_dmg(version, tag)
    else:
        print("Prebuilt packaging is configured for Windows and macOS only; run from source on Linux.", file=sys.stderr)
        return 1

    sums_file = DIST / "SHA256SUMS.txt"
    line = f"{sha256_file(artifact)}  {artifact.name}"
    sums_file.write_text(line + "\n", encoding="utf-8")

    print("\nBuilt release artifact:")
    print(" ", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
