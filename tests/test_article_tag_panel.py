"""文章视图右侧标签面板：展示文章标签 + 点击按标签筛选。"""

import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication, QPushButton

from quadrant_todo.db import Database
from quadrant_todo.app import MainWindow
from quadrant_todo.models import Article


def _chips(panel) -> list[QPushButton]:
    chips: list[QPushButton] = []
    for i in range(panel.chips.count()):
        item = panel.chips.itemAt(i)
        layout = item.layout() if item is not None else None
        if layout is None:
            continue
        for j in range(layout.count()):
            widget = layout.itemAt(j).widget()
            if isinstance(widget, QPushButton):
                chips.append(widget)
    return chips


def _build(window: MainWindow, db: Database) -> tuple[str, str]:
    a1, a2, a3 = Article(title="A"), Article(title="B"), Article(title="C")
    for a in (a1, a2, a3):
        db.save_article(a)
    t1 = db.create_tag("技术")
    t2 = db.create_tag("生活")
    db.set_article_tags(a1.id, [t1.id])
    db.set_article_tags(a2.id, [t1.id, t2.id])
    db.set_article_tags(a3.id, [t2.id])
    window.switch_view("article")
    return t1.id, t2.id


def test_article_view_shows_tag_panel() -> None:
    db = Database(Path(tempfile.mkdtemp()) / "t.db")
    db.connect()
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(db)
    window.show()
    _build(window, db)

    assert window.article_tag_panel.isVisible()
    assert not window.detail.isVisible()

    names = [c.text() for c in _chips(window.article_tag_panel)]
    assert any(n.startswith("全部（3）") for n in names), names
    assert any(n.startswith("技术（2）") for n in names), names


def test_click_tag_filters_articles_and_toggles_off() -> None:
    db = Database(Path(tempfile.mkdtemp()) / "t.db")
    db.connect()
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(db)
    window.show()
    _build(window, db)

    tech = next(c for c in _chips(window.article_tag_panel) if c.text().startswith("技术"))
    tech.click()
    assert window.article_view.list_widget.count() == 2
    assert "技术" in window.article_view.title_label.text()

    tech = next(c for c in _chips(window.article_tag_panel) if c.text().startswith("技术"))
    tech.click()
    assert window.article_view.list_widget.count() == 3
    assert window.article_tag_filter is None


def test_leaving_article_view_restores_detail_panel() -> None:
    db = Database(Path(tempfile.mkdtemp()) / "t.db")
    db.connect()
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(db)
    window.show()
    _build(window, db)

    window.switch_view("board")
    assert window.detail.isVisible()
    assert not window.article_tag_panel.isVisible()
