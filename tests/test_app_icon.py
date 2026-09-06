"""应用图标回归测试。

覆盖两个曾导致「任务栏图标异常」的坑：
1. ICO 必须包含 32/48 等任务栏尺寸（托盘只用 16，容易误判为正常）。
2. 打包后图标资源位于 sys._MEIPASS/_internal 下，而非 exe 同级目录。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_icon_file_exists() -> None:
    icon = PROJECT_ROOT / "assets" / "app.ico"
    assert icon.exists(), f"缺少图标文件：{icon}"


def test_icon_has_taskbar_sizes() -> None:
    """任务栏需要 32/48 尺寸；只有 16 时托盘正常但任务栏显示异常。"""
    from PIL import Image

    icon = PROJECT_ROOT / "assets" / "app.ico"
    with Image.open(icon) as img:
        sizes = {tuple(s) for s in img.info.get("sizes", set())}
    assert (32, 32) in sizes, f"图标缺少 32x32（任务栏尺寸），实际 {sorted(sizes)}"
    assert (48, 48) in sizes, f"图标缺少 48x48（任务栏尺寸），实际 {sorted(sizes)}"
    assert len(sizes) >= 4, f"图标尺寸过少：{sorted(sizes)}"


def test_icon_path_resolves_to_meipass_when_frozen(tmp_path, monkeypatch) -> None:
    """打包态：图标应解析到 sys._MEIPASS/assets/app.ico。

    直接调用 _icon_path() 而非 reload 模块——reload 会把模块级常量换成临时
    目录里的假图标，污染同会话内后续用例。
    """
    meipass = tmp_path / "_internal"
    (meipass / "assets").mkdir(parents=True)
    (meipass / "assets" / "app.ico").write_bytes(b"icon")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    from quadrant_todo import config

    resolved = config._icon_path()
    assert resolved == meipass / "assets" / "app.ico"
    assert resolved.exists()


def test_icon_path_prefers_meipass_over_anchor(tmp_path, monkeypatch) -> None:
    """打包态下 _MEIPASS 优先于 exe 同级目录（后者实际不存在资源）。"""
    meipass = tmp_path / "_internal"
    (meipass / "assets").mkdir(parents=True)
    (meipass / "assets" / "app.ico").write_bytes(b"icon")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    from quadrant_todo import config

    assert config._icon_path().parent.parent == meipass


def test_icon_path_falls_back_to_project_root() -> None:
    """开发态：未打包时应解析到项目根 assets/app.ico。"""
    from quadrant_todo import config

    assert not getattr(sys, "frozen", False)
    resolved = config._icon_path()
    assert resolved.exists(), f"开发态图标不存在：{resolved}"
    assert resolved == PROJECT_ROOT / "assets" / "app.ico"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.mark.parametrize("size", [16, 32, 48, 256])
def test_icon_loads_in_qt(qapp, size: int) -> None:
    """Qt 能加载图标并提供对应尺寸（保证 setWindowIcon 真正生效）。"""
    from PySide6.QtGui import QIcon

    from quadrant_todo import config

    icon = QIcon(str(config.APP_ICON_PATH))
    assert not icon.isNull(), "Qt 无法加载 app.ico"
    assert not icon.pixmap(size, size).isNull(), f"缺少 {size}x{size} 尺寸"
