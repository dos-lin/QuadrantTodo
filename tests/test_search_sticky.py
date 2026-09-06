"""搜索同时覆盖小便签（2026-09-03 用户要求）。

回归用例 A：db.search_stickies 按内容模糊匹配便签
回归用例 B：SearchView 便签命中区
- 有命中 → 标题「小便签 · N 条匹配（点击查看）」+ 便签卡片，点击发 sticky_open_requested
- 无命中 → 区块隐藏
回归用例 C：StickyView 按搜索词过滤展示
- 带 keyword → 标题含「匹配 N 条」，显示「清除筛选」按钮，点击发 filter_clear_requested
- 无 keyword → 标题「小便签」，隐藏「清除筛选」
"""

import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from quadrant_todo.db import Database
from quadrant_todo.models import StickyNote
from quadrant_todo.views.search import SearchView
from quadrant_todo.views.sticky import StickyView


def _make_db() -> Database:
    db = Database(Path(tempfile.mkdtemp(prefix="qtodo_search_sticky_")) / "data.db")
    db.connect()
    return db


class SearchStickiesDbTest(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()
        self.db.save_sticky(StickyNote(content="每周复盘会议记录"))
        self.db.save_sticky(StickyNote(content="购物清单：牛奶"))
        self.db.save_sticky(StickyNote(content="Buy Milk tomorrow"))

    def test_match_by_substring(self):
        hits = self.db.search_stickies("复盘")
        self.assertEqual([n.content for n in hits], ["每周复盘会议记录"])

    def test_no_match_returns_empty(self):
        self.assertEqual(self.db.search_stickies("不存在的词"), [])

    def test_ascii_case_insensitive_like(self):
        # SQLite LIKE 对 ASCII 大小写不敏感：小写 milk 命中 "Buy Milk tomorrow"
        self.assertEqual([n.content for n in self.db.search_stickies("milk")],
                         ["Buy Milk tomorrow"])


class SearchViewStickySectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = SearchView()

    def _note(self, content: str) -> StickyNote:
        return StickyNote(content=content, color="#FFF9C4")

    def test_hits_visible_with_cards(self):
        self.view.render_stickies([self._note("第一条"), self._note("第二条")])
        self.assertTrue(self.view.sticky_scroll.isVisibleTo(self.view))
        self.assertIn("2 条匹配", self.view.sticky_header.text())
        self.assertEqual(self.view.sticky_area.count(), 3)  # 2 卡片 + stretch

    def test_no_hits_hides_section(self):
        self.view.render_stickies([])
        self.assertFalse(self.view.sticky_scroll.isVisibleTo(self.view))
        self.assertEqual(self.view.sticky_header.text(), "")

    def test_click_card_emits_open_requested(self):
        received = []
        self.view.sticky_open_requested.connect(lambda: received.append(True))
        self.view.render_stickies([self._note("点我")])
        self.view.sticky_area.itemAt(0).widget().click()
        self.assertEqual(received, [True])


class StickyViewFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = StickyView()

    def test_render_with_keyword(self):
        self.view.render([StickyNote(content="复盘")], keyword="复盘")
        self.assertIn("匹配 1 条", self.view.title.text())
        self.assertTrue(self.view.clear_filter_btn.isVisibleTo(self.view))

    def test_render_without_keyword(self):
        self.view.render([StickyNote(content="随便")])
        self.assertEqual(self.view.title.text(), "小便签")
        self.assertFalse(self.view.clear_filter_btn.isVisibleTo(self.view))

    def test_clear_filter_signal(self):
        received = []
        self.view.filter_clear_requested.connect(lambda: received.append(True))
        self.view.clear_filter_btn.click()
        self.assertEqual(received, [True])


if __name__ == "__main__":
    unittest.main()
