"""PyInstaller 打包入口。

quadrant_todo/main.py 使用相对导入，直接作为 PyInstaller 顶层脚本运行时会因
"no known parent package" 失败。因此提供此顶层入口，以绝对导入方式启动应用。
"""
from __future__ import annotations

import sys

from quadrant_todo.main import run

if __name__ == "__main__":
    sys.exit(run())
