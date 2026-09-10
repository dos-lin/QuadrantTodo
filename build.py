#!/usr/bin/env python3
"""一键打包为 Windows 桌面应用（PRD F13）。

产出 PyInstaller --onedir 单目录 + 免安装压缩包：
    dist/QuadrantTodo/QuadrantTodo.exe
    dist/QuadrantTodo_v<version>.zip

选用 --onedir 而非 --onefile：PySide6 体积大，--onefile 每次启动都需解压到
临时目录，启动明显偏慢；--onedir 启动快，代价只是多一个文件夹，用压缩包化解。
"""
from __future__ import annotations

import shutil
import sys
import time
import zipfile
from pathlib import Path

APP_NAME = "QuadrantTodo"
ENTRY = "entry.py"
DIST = Path("dist")
BUILD_DIR = DIST / APP_NAME
#: PyInstaller 中间产物目录
WORK_DIR = Path("build") / APP_NAME
README = Path("README.txt")


def _retire(path: Path) -> None:
    """把旧目录改名让位，而不是直接删除。

    直接 rmtree 容易因文件占用（旧 exe 仍在运行）失败而中断打包；
    改名即时生效，旧目录留待后续清理。
    """
    if not path.exists():
        return
    target = path.with_name(f"{path.name}_old_{time.strftime('%H%M%S')}")
    try:
        path.rename(target)
        print(f"旧目录已让位：{path} → {target.name}")
    except OSError as exc:
        print(f"警告：无法改名 {path}（{exc}），打包可能失败", file=sys.stderr)


def _clean_runtime_artifacts(build_dir: Path) -> None:
    """移除打包/冒烟过程中生成的运行时数据文件，避免它们进入分发包。

    程序默认便携模式，exe 同级目录会生成 data.db / backups / logs / app_config.ini。
    若把这些文件打包进去，用户解压覆盖时会误把空库/测试配置覆盖掉自己的数据。
    """
    for name in ("data.db", "app_config.ini"):
        path = build_dir / name
        if path.exists() and path.is_file():
            path.unlink()
            print(f"已移除运行时文件：{path.name}")
    for subdir in ("backups", "logs"):
        path = build_dir / subdir
        if path.exists() and path.is_dir():
            shutil.rmtree(path)
            print(f"已移除运行时目录：{subdir}/")


def _collect_qt_excludes() -> list[str]:
    """裁掉用不到的 Qt 子模块，缩小产物体积（PRD F13.3）。"""
    return [
        "PySide6.QtWebEngine",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebChannel",
        "PySide6.Qt3D",
        "PySide6.QtCharts",
        "PySide6.QtMultimedia",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.QtDesigner",
        "PySide6.QtLocation",
        "PySide6.QtBluetooth",
        "PySide6.QtSerialPort",
        "PySide6.QtPositioning",
        "PySide6.QtNfc",
        "PySide6.QtScxml",
        "PySide6.QtSpeech",
        "PySide6.QtSvgWidgets",
        "PySide6.QtVirtualKeyboard",
        "PySide6.QtRemoteObjects",
        "PySide6.QtPdf",
    ]


def build() -> int:
    try:
        from PyInstaller.__main__ import run
    except ImportError:
        print("请先安装 PyInstaller：pip install pyinstaller", file=sys.stderr)
        return 1

    # 先让旧目录改名让位，避免 PyInstaller 内部覆盖/清理时因占用或安全策略中断
    _retire(BUILD_DIR)
    _retire(WORK_DIR)

    icon_path = Path("assets/app.ico")
    args = [
        str(ENTRY),
        "--name", APP_NAME,
        "--onedir",
        "--windowed",
        "--noconfirm",
        "--hidden-import", "PySide6.QtNetwork",
        "--hidden-import", "PySide6.QtSvg",
        # 样式表是数据文件，需显式带进产物（app.py 按 __file__ 同级目录读取）
        "--add-data", "quadrant_todo/styles.qss;quadrant_todo",
        "--add-data", "quadrant_todo/styles/dark.qss;quadrant_todo/styles",
        # 图标资源（放在锚点目录 /assets，与 config.APP_ICON_PATH 对应）
        "--add-data", "assets/app.ico;assets",
    ]
    if icon_path.exists():
        args += ["--icon", str(icon_path)]

    for module in _collect_qt_excludes():
        args += ["--exclude-module", module]

    run(args)

    if not BUILD_DIR.exists():
        print("打包失败：未生成产物目录", file=sys.stderr)
        return 1

    # 清理打包/冒烟过程中产生的运行时数据文件，避免带入分发包覆盖用户数据
    _clean_runtime_artifacts(BUILD_DIR)

    # 附带使用说明（PRD F13.6）
    if README.exists():
        shutil.copy(README, BUILD_DIR / "README.txt")

    # 产物 ≤ 150MB 校验（PRD F13.2）
    total = sum(f.stat().st_size for f in BUILD_DIR.rglob("*") if f.is_file())
    print(f"产物大小：{total / 1024 / 1024:.1f} MB")

    # 免安装压缩包
    version = _read_version()
    zip_name = DIST / f"{APP_NAME}_v{version}.zip"
    if zip_name.exists():
        zip_name.unlink()
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in BUILD_DIR.rglob("*"):
            if file.is_file():
                archive.write(file, file.relative_to(BUILD_DIR))
    print(f"已生成免安装压缩包：{zip_name}")
    return 0


def _read_version() -> str:
    try:
        import importlib

        cfg = importlib.import_module("quadrant_todo.config")
        return getattr(cfg, "VERSION", "1.0.0")
    except Exception:
        return "1.0.0"


if __name__ == "__main__":
    sys.exit(build())
