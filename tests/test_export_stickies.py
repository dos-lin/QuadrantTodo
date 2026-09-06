"""数据导出/导入应包含便签（2026-09-04 用户反馈）。

回归用例：F10.6 导出除任务外还应包含小便签，导入可完整还原。
"""

import json
import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from quadrant_todo.db import Database
from quadrant_todo.models import StickyNote, Task


def _make_db() -> Database:
    db = Database(Path(tempfile.mkdtemp(prefix="qtodo_export_sticky_")) / "data.db")
    db.connect()
    return db


class ExportStickiesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.db = _make_db()
        self.db.save_task(Task(title="写周报"))
        self.db.save_sticky(StickyNote(content="随手记一条想法", color="#FFF9C4"))
        self.db.save_sticky(StickyNote(content="明天记得买牛奶", pinned=True))

    def test_export_payload_includes_stickies(self):
        payload = {
            "format": "quadrant-todo-export",
            "version": 1,
            "tasks": [t.to_export() for t in self.db.all_tasks()],
            "stickies": [n.to_export() for n in self.db.get_stickies()],
        }
        self.assertEqual(len(payload["stickies"]), 2, "导出应包含全部便签")
        contents = {s["content"] for s in payload["stickies"]}
        self.assertIn("随手记一条想法", contents)
        self.assertIn("明天记得买牛奶", contents)

    def test_roundtrip_stickies(self):
        """导出 → 清空 → 导入（from_export）→ 还原。"""
        exported = [n.to_export() for n in self.db.get_stickies()]

        self.db.clear_stickies()
        self.assertEqual(self.db.get_stickies(), [], "清空后便签应为空")

        for item in exported:
            self.db.save_sticky(StickyNote.from_export(item))

        restored = self.db.get_stickies()
        self.assertEqual(len(restored), 2, "导入后便签数量应还原")
        self.assertEqual(
            sorted(n.content for n in restored),
            ["明天记得买牛奶", "随手记一条想法"],
        )
        pinned = [n for n in restored if n.pinned]
        self.assertEqual(len(pinned), 1, "置顶状态应一并还原")
