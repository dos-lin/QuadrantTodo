"""增量导入（2026-09-11）：不清空原有数据。

规则：
* 同 id 且数据完全一致 → 覆盖（重复导入同一文件幂等，不产生副本）
* 同 id 但数据不同     → 换新 id 追加，本地原记录保留
* 本地无此 id          → 新增
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from quadrant_todo.app import EXPORT_FORMAT, EXPORT_VERSION, MainWindow, _merge_for_import
from quadrant_todo.db import Database
from quadrant_todo.models import Article, StickyNote, Task


class MergeForImportTest(unittest.TestCase):
    """落库策略（纯逻辑层）。"""

    def test_unknown_id_kept_as_is(self) -> None:
        obj = Task(title="新任务")
        result = _merge_for_import(obj, {})
        self.assertIs(result, obj)
        self.assertEqual(result.title, "新任务")

    def test_identical_record_keeps_id(self) -> None:
        """同 id 且数据完全一致 → 覆盖，id 不变。"""
        old = Task(title="原任务")
        same = Task.from_export(old.to_export())
        self.assertEqual(_merge_for_import(same, {old.id: old}).id, old.id)

    def test_different_record_gets_new_id(self) -> None:
        """同 id 但数据不同 → 换新 id 追加，不动原记录。"""
        old = Task(title="原任务")
        changed = Task.from_export(old.to_export())
        changed.title = "改过的任务"
        result = _merge_for_import(changed, {old.id: old})
        self.assertNotEqual(result.id, old.id)
        self.assertEqual(result.title, "改过的任务")

    def test_works_for_sticky_and_article(self) -> None:
        old_note = StickyNote(content="原便签")
        changed_note = StickyNote.from_export(old_note.to_export())
        changed_note.content = "改过的便签"
        self.assertNotEqual(
            _merge_for_import(changed_note, {old_note.id: old_note}).id, old_note.id
        )

        old_art = Article(title="原文章")
        changed_art = Article.from_export(old_art.to_export())
        changed_art.content = "新的正文"
        self.assertNotEqual(
            _merge_for_import(changed_art, {old_art.id: old_art}).id, old_art.id
        )


class IncrementalImportTest(unittest.TestCase):
    """走 MainWindow.import_data 真实链路（屏蔽文件/弹窗交互）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.db = Database()
        self.db.connect()
        self.win = MainWindow(self.db)
        self.tmp = tempfile.mkdtemp(prefix="qtodo_import_")

    def tearDown(self):
        self.win.close()
        self.db.close()

    # ---------------------------------------------------------------- 工具

    def _write_payload(self, payload: dict) -> str:
        path = os.path.join(self.tmp, "import.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return path

    def _run_import(self, path: str) -> None:
        with patch.object(QFileDialog, "getOpenFileName", return_value=(path, "")), \
             patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
             patch.object(QMessageBox, "information", return_value=QMessageBox.Ok):
            self.win.import_data()

    def _payload(self, **kwargs) -> dict:
        base = {"format": EXPORT_FORMAT, "version": EXPORT_VERSION}
        base.update(kwargs)
        return base

    # ---------------------------------------------------------------- 用例

    def test_existing_data_not_cleared_and_diff_appended(self) -> None:
        """原有任务/便签保留；同 id 但不同 → 追加为新记录。"""
        tag = uuid.uuid4().hex[:8]
        local_title, changed_title, fresh_title = (
            f"本地任务-{tag}", f"导入版任务-{tag}", f"导入新增任务-{tag}",
        )
        local = Task(title=local_title)
        self.db.save_task(local)
        self.db.save_sticky(StickyNote(content=f"本地便签-{tag}"))

        changed = Task.from_export(local.to_export())
        changed.title = changed_title
        fresh = Task(title=fresh_title)
        path = self._write_payload(self._payload(
            tasks=[changed.to_export(), fresh.to_export()],
            stickies=[],
            articles=[],
            articleTags=[],
        ))
        self._run_import(path)

        titles = [t.title for t in self.db.all_tasks()]
        self.assertIn(local_title, titles, "原有任务不应被覆盖或清空")
        self.assertIn(changed_title, titles, "有差异的记录应作为新记录追加")
        self.assertIn(fresh_title, titles)
        self.assertTrue(
            any(n.content == f"本地便签-{tag}" for n in self.db.get_stickies()),
            "便签不应被清空",
        )

    def test_reimport_same_file_is_idempotent(self) -> None:
        """同一文件重复导入不产生副本。"""
        title = f"唯一任务-{uuid.uuid4().hex[:8]}"
        task = Task(title=title)
        payload = self._payload(
            tasks=[task.to_export()], stickies=[], articles=[], articleTags=[]
        )
        path = self._write_payload(payload)

        def count() -> int:
            return sum(1 for t in self.db.all_tasks() if t.title == title)

        self._run_import(path)
        self.assertEqual(count(), 1)
        self._run_import(path)
        self.assertEqual(count(), 1, "重复导入不应产生副本")

    def test_articles_imported_incrementally(self) -> None:
        tag = uuid.uuid4().hex[:8]
        local_title, new_title = f"本地文章-{tag}", f"导入的新文章-{tag}"
        local_art = Article(title=local_title, content="本地正文")
        self.db.save_article(local_art)

        changed = Article.from_export(local_art.to_export())
        changed.content = "导入的正文"
        new_art = Article(title=new_title)
        path = self._write_payload(self._payload(
            tasks=[], stickies=[],
            articles=[changed.to_export(), new_art.to_export()],
            articleTags=[],
        ))
        self._run_import(path)

        # 原文保留 + 有差异的另存一份（标题相同） + 新增文章
        titles = sorted(a.title for a in self.db.get_articles() if tag in a.title)
        self.assertEqual(titles, sorted([local_title, local_title, new_title]))
        self.assertEqual(len(titles), 3)


if __name__ == "__main__":
    unittest.main()
