"""文章模块 + 便签置顶 + 子任务约束 专项测试（2026-09-10）。

覆盖：
- Article 模型 round-trip（to_row / from_row / to_export / from_export）
- StickyNote.top 字段持久化
- db 层：文章 CRUD / 搜索 / 便签置顶排序 / 未完成子任务计数
- ArticleView：搜索高亮 / 100KB 上限 / 渲染不报错
"""

from __future__ import annotations

import os
import tempfile
import unittest

from PySide6.QtWidgets import QApplication, QLabel

# offscreen 必须在 QApplication 创建前设置（CI 兼容）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from quadrant_todo.db import Database
from quadrant_todo.markdown import markdown_to_html
from quadrant_todo.models import Article, StickyNote, Subtask, Task
from quadrant_todo.views.article import ArticleView, MAX_ARTICLE_CHARS, _highlight


_app: QApplication | None = None


def _get_app() -> QApplication:
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


def _make_db() -> Database:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # Database 会自己建
    db = Database(path)
    db.connect()
    return db


class TestArticleModel(unittest.TestCase):
    def test_row_roundtrip(self):
        a = Article(id="aid", title="标题", content="正文 **md**")
        row = a.to_row()
        self.assertEqual(row["title"], "标题")
        self.assertEqual(row["content"], "正文 **md**")
        back = Article.from_row(row)
        self.assertEqual(back.id, a.id)
        self.assertEqual(back.title, a.title)
        self.assertEqual(back.content, a.content)

    def test_export_roundtrip(self):
        a = Article(id="aid2", title="T2", content="C2")
        data = a.to_export()
        self.assertEqual(data["createdAt"], a.created_at.isoformat())
        back = Article.from_export(data)
        self.assertEqual(back.id, a.id)
        self.assertEqual(back.title, a.title)
        self.assertEqual(back.content, a.content)

    def test_top_field_persisted_in_sticky(self):
        n = StickyNote(content="x", top=True)
        row = n.to_row()
        self.assertEqual(row["top"], 1)
        back = StickyNote.from_row(row)
        self.assertTrue(back.top)
        self.assertFalse(StickyNote(content="y", top=False).to_row()["top"])


class TestArticleDB(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()

    def tearDown(self):
        self.db.close()

    def test_article_crud(self):
        a = Article(id="a1", title="文章一", content="内容一")
        self.db.save_article(a)
        got = self.db.get_article("a1")
        self.assertIsNotNone(got)
        self.assertEqual(got.title, "文章一")

        got.title = "改后"
        got.content = "新内容"
        self.db.save_article(got)
        again = self.db.get_article("a1")
        self.assertEqual(again.title, "改后")
        self.assertEqual(again.content, "新内容")

        self.db.delete_article("a1")
        self.assertIsNone(self.db.get_article("a1"))

    def test_articles_ordered_by_updated_desc(self):
        # 先建旧文，再更新它，应排在最前
        self.db.save_article(Article(id="old", title="旧"))
        import time
        time.sleep(0.01)
        self.db.save_article(Article(id="new", title="新"))
        self.db.save_article(Article(id="old", title="旧-更新"))
        arts = self.db.get_articles()
        self.assertEqual([a.id for a in arts], ["old", "new"])

    def test_search_articles(self):
        self.db.save_article(Article(id="s1", title="苹果种植", content="如何种苹果"))
        self.db.save_article(Article(id="s2", title="橘子树", content="橘子与苹果的区别"))
        hits = self.db.search_articles("苹果")
        ids = {a.id for a in hits}
        self.assertIn("s1", ids)
        self.assertIn("s2", ids)
        title_only = self.db.search_articles("种植")
        self.assertEqual([a.id for a in title_only], ["s1"])

    def test_sticky_top_ordering(self):
        normal = StickyNote(content="普通", top=False)
        pinned = StickyNote(content="置顶", top=True)
        self.db.save_sticky(normal)
        self.db.save_sticky(pinned)
        notes = self.db.get_stickies()
        # 置顶的应排在普通之前
        self.assertEqual(notes[0].id, pinned.id)
        self.assertEqual(notes[1].id, normal.id)

    def test_incomplete_subtask_count(self):
        task = Task(title="主任务")
        self.db.save_task(task)
        self.db.save_subtask(Subtask(parent_id=task.id, title="子1", done=False))
        self.db.save_subtask(Subtask(parent_id=task.id, title="子2", done=False))
        self.assertEqual(self.db.get_incomplete_subtask_count(task.id), 2)
        # 完成一个
        subs = self.db.get_subtasks(task.id)
        subs[0].done = True
        self.db.save_subtask(subs[0])
        self.assertEqual(self.db.get_incomplete_subtask_count(task.id), 1)

    def test_article_tag_association(self):
        self.db.save_article(Article(id="ta", title="带标签文章"))
        self.db.set_article_tags("ta", ["t1", "t2"])
        self.assertEqual(set(self.db.get_article_tag_ids("ta")), {"t1", "t2"})
        self.db.set_article_tags("ta", ["t3"])
        self.assertEqual(self.db.get_article_tag_ids("ta"), ["t3"])


class TestArticleView(unittest.TestCase):
    def setUp(self):
        _get_app()

    def test_highlight(self):
        self.assertEqual(_highlight("hello world", "world"), "hello <b>world</b>")
        self.assertEqual(_highlight("a<b>c", "x"), "a&lt;b&gt;c")  # 转义安全

    def test_max_bytes_constant(self):
        self.assertEqual(MAX_ARTICLE_CHARS, 100_000)

    def test_render_and_search_highlight(self):
        view = ArticleView()
        articles = [
            Article(id="v1", title="苹果笔记", content="今天买了苹果"),
            Article(id="v2", title="普通日记", content="天气晴"),
        ]
        view.render(articles, keyword="苹果")
        self.assertEqual(view.list_widget.count(), 2)
        # 命中关键词的标题应被 <b> 高亮
        item_widget = view.list_widget.itemWidget(view.list_widget.item(0))
        self.assertIsNotNone(item_widget)
        texts = [c.text() for c in item_widget.findChildren(QLabel)]
        self.assertIn("<b>苹果</b>", "\n".join(texts))

    def test_over_limit_blocks_commit(self):
        view = ArticleView()
        view._selected_id = "x"
        view._loading = False
        huge = "中" * (MAX_ARTICLE_CHARS + 50)  # > 10万字
        view.content_edit.setPlainText(huge)
        view._on_content_changed()
        self.assertTrue(view._over_limit)
        self.assertIsNone(view._pending_content)

    def test_force_save_button_and_shortcut(self):
        """「保存」按钮 / Ctrl+S 应即时提交标题与正文，并显示保存中状态。"""
        view = ArticleView()
        view._selected_id = "x"
        view._loading = False
        view.title_edit.setText("标题")
        view.content_edit.setPlainText("正文")
        received = []
        view.update_requested.connect(lambda *args: received.append(args))

        # 模拟点击保存按钮
        view._force_save()

        self.assertEqual(len(received), 2, "应同时提交标题与正文")
        self.assertEqual(received[0], ("x", "title", "标题"))
        self.assertEqual(received[1], ("x", "content", "正文"))
        self.assertEqual(view.status_label.text(), "保存中…")

        # 保存按钮应存在（未 show 的 widget isVisible 可能为 False，只校验存在与文案）
        self.assertIsNotNone(view.save_btn)
        self.assertEqual(view.save_btn.text(), "保存")


class TestMarkdownConverter(unittest.TestCase):
    """纯函数单测：覆盖各语法与边界（不引入第三方 Markdown 库）。"""

    def test_empty(self):
        self.assertEqual(markdown_to_html(""), "")
        self.assertEqual(markdown_to_html(None), "")

    def test_headings(self):
        html = markdown_to_html("# 一\n## 二\n### 三")
        self.assertIn("<h1>一</h1>", html)
        self.assertIn("<h2>二</h2>", html)
        self.assertIn("<h3>三</h3>", html)

    def test_bold_italic(self):
        html = markdown_to_html("**粗** 和 *斜* 还有 _强调_")
        self.assertIn("<b>粗</b>", html)
        self.assertIn("<i>斜</i>", html)
        self.assertIn("<i>强调</i>", html)

    def test_inline_code(self):
        html = markdown_to_html("用 `code` 包裹")
        self.assertIn("<code>code</code>", html)

    def test_lists(self):
        ul = markdown_to_html("- 甲\n- 乙")
        self.assertIn("<ul><li>甲</li><li>乙</li></ul>", ul)
        ol = markdown_to_html("1. 一\n2. 二")
        self.assertIn("<ol><li>一</li><li>二</li></ol>", ol)

    def test_quote_and_hr(self):
        html = markdown_to_html("> 引用一行\n\n---\n\n正文")
        self.assertIn("<blockquote>引用一行</blockquote>", html)
        self.assertIn("<hr>", html)
        self.assertIn("<p>正文</p>", html)

    def test_link(self):
        html = markdown_to_html("看 [百度](https://baidu.com)")
        self.assertIn('<a href="https://baidu.com">百度</a>', html)

    def test_code_block_escaped(self):
        html = markdown_to_html("```\nif a < b: print(1)\n```")
        self.assertIn("&lt;", html)
        self.assertIn("<pre><code>", html)

    def test_image_dropped(self):
        html = markdown_to_html("有图 ![alt](http://x.com/a.png) 结束")
        self.assertNotIn("<img", html)
        self.assertNotIn("alt", html)

    def test_xss_escaped(self):
        html = markdown_to_html("<script>alert(1)</script> & <b>x</b>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&amp;", html)

    def test_underscore_in_link_not_italicized(self):
        html = markdown_to_html("见 [文档](https://a.com/b_c_d)")
        self.assertIn('<a href="https://a.com/b_c_d">文档</a>', html)


class TestArticleViewPreview(unittest.TestCase):
    """UI 层：编辑/预览切换的可见性与渲染结果。"""

    def setUp(self):
        _get_app()

    def _select(self, view, article):
        view.show()  # offscreen 下 show() 后子控件 isVisible() 才反映自身可见性
        view._selected_id = article.id
        view._rendered_selected_id = None
        view.render([article])

    def test_default_edit_mode(self):
        view = ArticleView()
        art = Article(id="a1", title="测试", content="# 标题\n\n正文")
        self._select(view, art)
        self.assertTrue(view.content_edit.isVisible())
        self.assertFalse(view.content_preview.isVisible())
        self.assertFalse(view._preview_mode)

    def test_toggle_to_preview_renders_and_swaps(self):
        view = ArticleView()
        art = Article(
            id="a1",
            title="测试",
            content="# 标题\n\n这是 **加粗** 和 *斜体*。\n\n- 项目一\n- 项目二",
        )
        self._select(view, art)
        view._on_mode_toggled(True)
        self.assertTrue(view._preview_mode)
        self.assertFalse(view.content_edit.isVisible())
        self.assertTrue(view.content_preview.isVisible())
        plain = view.content_preview.toPlainText()
        self.assertIn("标题", plain)
        self.assertIn("加粗", plain)
        self.assertIn("项目一", plain)

    def test_toggle_back_to_edit_keeps_source(self):
        view = ArticleView()
        art = Article(id="a1", title="测试", content="# 标题\n\n**加粗**")
        self._select(view, art)
        view._on_mode_toggled(True)
        view._on_mode_toggled(False)
        self.assertFalse(view._preview_mode)
        self.assertTrue(view.content_edit.isVisible())
        self.assertFalse(view.content_preview.isVisible())
        self.assertIn("**加粗**", view.content_edit.toPlainText())

    def test_preview_commits_pending_content(self):
        """预览前若有未提交改动，切到预览应先落库（emit update_requested）。"""
        view = ArticleView()
        art = Article(id="a1", title="测试", content="原内容")
        self._select(view, art)
        emitted = []
        view.update_requested.connect(
            lambda aid, field, val: emitted.append((aid, field, val))
        )
        view.content_edit.setPlainText("# 新标题\n\n新内容 **bold**")
        view._pending_content = "新内容 **bold**"
        view._on_mode_toggled(True)
        self.assertTrue(
            any(f == "content" for (_, f, _) in emitted),
            "切换到预览应触发 content 落库",
        )


if __name__ == "__main__":
    unittest.main()
