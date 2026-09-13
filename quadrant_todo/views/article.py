"""文章模块（2026-09-10）。

ArticleView：左侧搜索栏 + 文章标题列表，右侧标题 / 正文编辑 + 实时字数 + 标签。
- 正文编辑支持 Markdown 语法，并提供「编辑 / 预览」切换查看渲染效果
  （渲染用纯标准库 markdown_to_html 转换，不引入第三方 Markdown 库）
- 正文上限 10 万字；右下角实时字数「X 字 / 10万字」
- 搜索命中关键词在标题与预览片段中高亮（列表摘要保持原文片段，不渲染）
- 与任务 / 便签相互独立，复用 tag 表（article_tag 关联）但不与任务关联
- 标题 / 正文自动保存：改动后 600ms 无输入自动落库；同时提供「保存」按钮和 Ctrl+S 快捷键
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..markdown import markdown_to_html
from ..models import Article, Tag
from .common import clear_layout

#: 正文上限 10 万字（按字符数计，与左下角「字」统一维度）
MAX_ARTICLE_CHARS = 100_000


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _highlight(text: str, keyword: str) -> str:
    """转义后高亮关键词（大小写不敏感，非正则替换）。"""
    escaped = _escape(text)
    if not keyword:
        return escaped
    return escaped.replace(_escape(keyword), f"<b>{_escape(keyword)}</b>")


class ArticleView(QWidget):
    """文章列表 / 编辑页。"""

    add_requested = Signal(str)             # 新建文章（默认标题）
    update_requested = Signal(str, str, object)  # (id, field, value) field∈{title, content}
    delete_requested = Signal(str)
    search_requested = Signal(str)          # 搜索关键词（空字符串=清除筛选）
    tag_add_requested = Signal(str, str)    # (article_id, 标签名) 新建或复用
    tag_remove_requested = Signal(str, str) # (article_id, tag_id)
    selection_changed = Signal(str)         # 左侧列表切换选中文章（id）

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._articles: list[Article] = []
        self._keyword: str = ""
        self._selected_id: str | None = None
        self._rendered_selected_id: str | None = None
        self._all_tags: list[Tag] = []
        self._active_tag_ids: list[str] = []
        self._loading: bool = False
        self._over_limit: bool = False
        self._pending_title: str | None = None
        self._pending_content: str | None = None
        self._preview_mode: bool = False
        self._build_ui()

    # ------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        bar = QHBoxLayout()
        self.title_label = QLabel("文章")
        self.title_label.setProperty("role", "view-title")
        bar.addWidget(self.title_label, 1)
        root.addLayout(bar)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索文章标题或正文…")
        self.search_edit.textChanged.connect(self._on_search)
        root.addWidget(self.search_edit)

        splitter = QSplitter(Qt.Horizontal)

        # 左：列表
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.currentItemChanged.connect(self._on_item_selected)
        left_layout.addWidget(self.list_widget)
        splitter.addWidget(left)

        # 右：编辑
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("文章标题")
        self.title_edit.textChanged.connect(self._on_title_changed)
        right_layout.addWidget(self._row("标题", self.title_edit))

        # 工具栏：新建 / 保存 / 编辑 / 预览 / 删除
        tool_bar = QHBoxLayout()
        tool_bar.setSpacing(8)
        self.new_btn = QPushButton("新建文章")
        self.new_btn.setToolTip("新建一篇文章")
        self.new_btn.clicked.connect(lambda: self.add_requested.emit("无标题文章"))
        tool_bar.addWidget(self.new_btn)

        self.save_btn = QPushButton("保存")
        self.save_btn.setToolTip("立即保存（Ctrl+S）")
        self.save_btn.clicked.connect(self._force_save)
        tool_bar.addWidget(self.save_btn)

        tool_bar.addStretch(1)

        self._edit_btn = QPushButton("编辑")
        self._edit_btn.setCheckable(True)
        self._edit_btn.setChecked(True)
        self._edit_btn.clicked.connect(lambda: self._on_mode_toggled(False))
        tool_bar.addWidget(self._edit_btn)

        self._preview_btn = QPushButton("预览")
        self._preview_btn.setCheckable(True)
        self._preview_btn.clicked.connect(lambda: self._on_mode_toggled(True))
        tool_bar.addWidget(self._preview_btn)

        self.delete_btn = QPushButton("删除文章")
        self.delete_btn.setProperty("danger", True)
        self.delete_btn.clicked.connect(self._on_delete)
        tool_bar.addWidget(self.delete_btn)
        right_layout.addLayout(tool_bar)

        self.content_edit = QPlainTextEdit()
        self.content_edit.setPlaceholderText("正文（支持 Markdown，点击「预览」查看渲染效果）")
        self.content_edit.setMinimumHeight(240)
        self.content_edit.textChanged.connect(self._on_content_changed)
        right_layout.addWidget(self.content_edit, 1)

        # 预览：只读富文本，渲染 markdown_to_html 结果
        self.content_preview = QTextEdit()
        self.content_preview.setReadOnly(True)
        self.content_preview.setVisible(False)
        right_layout.addWidget(self.content_preview, 1)

        # 字数、创建/修改时间、保存状态放在同一行
        meta_bar = QHBoxLayout()
        self.word_label = QLabel()
        self.word_label.setProperty("role", "meta")
        meta_bar.addWidget(self.word_label)
        self.meta_label = QLabel()
        self.meta_label.setProperty("role", "meta")
        meta_bar.addWidget(self.meta_label)
        meta_bar.addStretch(1)
        self.status_label = QLabel("未选择文章")
        self.status_label.setProperty("role", "meta")
        meta_bar.addWidget(self.status_label)
        right_layout.addLayout(meta_bar)

        right_layout.addWidget(self._build_tags_ui())

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        # 防抖提交（避免逐字触发整页刷新导致焦点/光标跳动）
        self._title_timer = QTimer(self)
        self._title_timer.setSingleShot(True)
        self._title_timer.timeout.connect(self._commit_title)
        self._content_timer = QTimer(self)
        self._content_timer.setSingleShot(True)
        self._content_timer.timeout.connect(self._commit_content)

        # Ctrl+S 立即保存（也作为保存按钮的显式入口）
        self._save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self._save_shortcut.activated.connect(self._force_save)

    def _row(self, label: str, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        cap = QLabel(label)
        cap.setProperty("role", "field-label")
        layout.addWidget(cap)
        layout.addWidget(widget)
        return container

    def _build_tags_ui(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        cap = QLabel("标签")
        cap.setProperty("role", "field-label")
        layout.addWidget(cap)
        self.tag_chip_row = QHBoxLayout()
        self.tag_chip_widget = QWidget()
        self.tag_chip_widget.setLayout(self.tag_chip_row)
        self.tag_chip_row.setContentsMargins(0, 0, 0, 0)
        self.tag_chip_row.setSpacing(4)
        layout.addWidget(self.tag_chip_widget)
        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("输入标签名后回车添加")
        self.tag_input.returnPressed.connect(self._on_tag_committed)
        layout.addWidget(self.tag_input)
        return container

    # ------------------------------------------------------------ 渲染

    def render(self, articles, keyword: str | None = None,
               all_tags: list[Tag] | None = None,
               active_tag_ids: list[str] | None = None) -> None:
        self._articles = list(articles)
        self._keyword = keyword or ""
        if all_tags is not None:
            self._all_tags = list(all_tags)
        if active_tag_ids is not None:
            self._active_tag_ids = list(active_tag_ids)

        self.title_label.setText(
            f"文章 · 「{self._keyword}」匹配 {len(articles)} 篇" if self._keyword
            else "文章"
        )

        # 重建左侧列表（blockSignals 避免重建时误触发选中）
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for art in self._articles:
            item = QListWidgetItem()
            widget = self._build_list_item(art)
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)
            item.setSizeHint(widget.sizeHint())
            if art.id == self._selected_id:
                item.setSelected(True)
                self.list_widget.setCurrentItem(item)
        self.list_widget.blockSignals(False)

        # 右侧：仅当选中文章变化或首次时重填，避免编辑中刷新打断光标
        sel = next((a for a in self._articles if a.id == self._selected_id), None)
        if sel is not None:
            if self._selected_id != self._rendered_selected_id:
                self._show_article(sel)
                self._rendered_selected_id = self._selected_id
            else:
                # 同一篇文章：刷新标签，并在没有待提交改动时显示已保存
                self._render_tags()
                if not (self._title_timer.isActive() or self._content_timer.isActive()):
                    self._set_status("已保存", "meta")
        else:
            self._clear_right()
            self._rendered_selected_id = None

    def _build_list_item(self, article: Article) -> QWidget:
        frame = QFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        title = QLabel()
        title.setProperty("role", "article-title")
        title.setText(f"<b>{_highlight(article.title or '无标题文章', self._keyword)}</b>")
        title.setWordWrap(True)
        layout.addWidget(title)

        preview_text = (article.content or "").replace("\n", " ").strip()
        if len(preview_text) > 60:
            preview_text = preview_text[:60] + "…"
        preview = QLabel()
        preview.setProperty("role", "meta")
        preview.setText(_highlight(preview_text, self._keyword))
        preview.setWordWrap(True)
        layout.addWidget(preview)
        return frame

    def _show_article(self, article: Article) -> None:
        self._loading = True
        self.title_edit.setReadOnly(False)
        self.title_edit.setPlaceholderText("文章标题")
        self.content_edit.setReadOnly(False)
        self.content_edit.setPlaceholderText("正文（支持 Markdown，点击「预览」查看渲染效果）")
        self.title_edit.setText(article.title or "")
        self.content_edit.setPlainText(article.content or "")
        self._update_word_count(article.content or "")
        self._update_meta_label(article)
        self._render_tags()
        self._loading = False
        # 按当前模式呈现正文（预览模式下直接渲染新文章）
        self._apply_mode()

    def _clear_right(self) -> None:
        self._loading = True
        self.title_edit.setReadOnly(True)
        self.title_edit.setPlaceholderText("选择或新建一篇文章以开始编辑")
        self.title_edit.clear()
        self.content_edit.setReadOnly(True)
        self.content_edit.setPlaceholderText("选择或新建一篇文章以开始编辑")
        self.content_edit.clear()
        # 复位到编辑模式
        self._preview_mode = False
        self._edit_btn.setChecked(True)
        self._preview_btn.setChecked(False)
        self.content_preview.setVisible(False)
        self.content_edit.setVisible(True)
        self.word_label.clear()
        self.meta_label.clear()
        self._set_status("未选择文章", "meta")
        clear_layout(self.tag_chip_row)
        self.tag_chip_row.addStretch(1)
        self._loading = False

    def _render_tags(self) -> None:
        clear_layout(self.tag_chip_row)
        active = set(self._active_tag_ids)
        for tag in self._all_tags:
            if tag.id not in active:
                continue
            chip = QPushButton(tag.name)
            chip.setFlat(True)
            chip.setProperty("tagcolor", tag.color)
            chip.setToolTip("点击移除标签")
            chip.clicked.connect(
                lambda _c=False, tid=tag.id: self.tag_remove_requested.emit(self._selected_id, tid)
            )
            self.tag_chip_row.addWidget(chip)
        self.tag_chip_row.addStretch(1)

    # ------------------------------------------------------------ 交互

    def _on_item_selected(self, current, _previous) -> None:
        if current is None:
            return
        row = self.list_widget.row(current)
        if row < 0 or row >= len(self._articles):
            return
        article = self._articles[row]
        self._selected_id = article.id
        # 通过信号让应用层重新渲染右侧标签与时间，避免上一篇文章标签残留
        self.selection_changed.emit(article.id)

    def _on_search(self, text: str) -> None:
        self.search_requested.emit(text.strip())

    def _on_mode_toggled(self, preview: bool) -> None:
        """切换 编辑 / 预览 模式。"""
        if preview == self._preview_mode:
            return
        self._preview_mode = preview
        self._edit_btn.setChecked(not preview)
        self._preview_btn.setChecked(preview)
        self._apply_mode()

    def _apply_mode(self) -> None:
        """根据当前模式呈现正文区。预览模式下若有未提交改动先落库，再渲染。"""
        if self._preview_mode:
            if self._pending_content is not None:
                self._content_timer.stop()
                self._commit_content()
            self.content_preview.setHtml(
                markdown_to_html(self.content_edit.toPlainText())
            )
            self.content_edit.setVisible(False)
            self.content_preview.setVisible(True)
        else:
            self.content_preview.setVisible(False)
            self.content_edit.setVisible(True)

    def _on_title_changed(self) -> None:
        if self._loading or not self._selected_id:
            return
        self._set_status("未保存（Ctrl+S 或自动保存）", "meta")
        self._pending_title = self.title_edit.text()
        self._title_timer.stop()
        self._title_timer.start(600)

    def _commit_title(self) -> None:
        if self._loading or not self._selected_id or self._pending_title is None:
            return
        title = self._pending_title.strip() or "无标题文章"
        self._pending_title = None
        self._set_status("保存中…", "meta")
        self.update_requested.emit(self._selected_id, "title", title)

    def _on_content_changed(self) -> None:
        if self._loading or not self._selected_id:
            return
        text = self.content_edit.toPlainText()
        if len(text) > MAX_ARTICLE_CHARS:
            self._over_limit = True
            self.word_label.setProperty("role", "error")
            self.word_label.setText(f"{len(text)} 字 / 10万字 —— 已达上限，请删减")
            self.style().unpolish(self.word_label)
            self.style().polish(self.word_label)
            self._set_status("内容超过上限，无法保存", "error")
            return
        self._over_limit = False
        self._update_word_count(text)
        self._set_status("未保存（Ctrl+S 或自动保存）", "meta")
        self._pending_content = text
        self._content_timer.stop()
        self._content_timer.start(600)

    def _commit_content(self) -> None:
        if self._loading or not self._selected_id or self._pending_content is None:
            return
        self._pending_content = None
        if self._over_limit:
            return
        self._set_status("保存中…", "meta")
        self.update_requested.emit(
            self._selected_id, "content", self.content_edit.toPlainText()
        )

    def _update_word_count(self, text: str) -> None:
        if self._over_limit:
            return
        self.word_label.setProperty("role", "meta")
        self.word_label.setText(f"{len(text)} 字 / 10万字")
        self.style().unpolish(self.word_label)
        self.style().polish(self.word_label)

    def _update_meta_label(self, article: Article) -> None:
        def _fmt(dt: Optional[datetime]) -> str:
            if dt is None:
                return "-"
            return dt.strftime("%Y-%m-%d %H:%M")
        created = _fmt(article.created_at)
        updated = _fmt(article.updated_at)
        self.meta_label.setText(f"创建：{created}　修改：{updated}")

    def _on_tag_committed(self) -> None:
        if not self._selected_id:
            return
        name = self.tag_input.text().strip()
        if not name:
            return
        self.tag_input.clear()
        self.tag_add_requested.emit(self._selected_id, name)

    def _on_delete(self) -> None:
        if not self._selected_id:
            return
        self.delete_requested.emit(self._selected_id)

    def _set_status(self, text: str, role: str = "meta") -> None:
        self.status_label.setProperty("role", role)
        self.status_label.setText(text)
        self.style().unpolish(self.status_label)
        self.style().polish(self.status_label)

    def _force_save(self) -> None:
        if not self._selected_id or self._loading or self._over_limit:
            if self._over_limit:
                self._set_status("内容超过上限，无法保存", "error")
            return
        self._title_timer.stop()
        self._content_timer.stop()
        title = self.title_edit.text().strip() or "无标题文章"
        content = self.content_edit.toPlainText()
        self._pending_title = None
        self._pending_content = None
        self._set_status("保存中…", "meta")
        self.update_requested.emit(self._selected_id, "title", title)
        self.update_requested.emit(self._selected_id, "content", content)
