"""文章视图的右侧标签面板。

切换到「文章」时占用右侧常驻栏（替代任务详情面板），展示文章用到的全部标签；
点击标签 = 按该标签筛选文章列表，再点一次取消筛选。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..models import Tag
from .common import clear_layout


class ArticleTagPanel(QFrame):
    """文章标签面板（点击即按标签筛选）。"""

    tag_selected = Signal(str)   # tag_id
    cleared = Signal()           # 取消标签筛选

    #: 每行放几个标签（面板固定宽度 320，2 个一行排版最稳）
    _PER_ROW = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active_id: str | None = None
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumWidth(320)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("文章标签")
        title.setProperty("role", "view-title")
        root.addWidget(title)

        self.hint_label = QLabel("还没有文章标签")
        self.hint_label.setProperty("role", "empty-hint")
        self.hint_label.setWordWrap(True)
        root.addWidget(self.hint_label)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chip_widget = QWidget()
        self.chips = QVBoxLayout(self.chip_widget)
        self.chips.setContentsMargins(0, 0, 0, 0)
        self.chips.setSpacing(6)
        area.setWidget(self.chip_widget)
        root.addWidget(area, 1)

    # ---------------------------------------------------------------- 渲染

    def render(self, tags: list[Tag], counts: dict[str, int],
               active_id: str | None, total: int = 0) -> None:
        """渲染标签 chip。tags 为已被文章使用的标签，counts 为各自的文章数。"""
        self._active_id = active_id
        self.hint_label.setVisible(not tags)

        clear_layout(self.chips)
        if not tags:
            return

        chips: list[QPushButton] = []

        all_btn = QPushButton(f"全部（{total}）")
        all_btn.setCheckable(True)
        all_btn.setChecked(active_id is None)
        all_btn.clicked.connect(self.cleared.emit)
        chips.append(all_btn)

        for tag in tags:
            chip = QPushButton(f"{tag.name}（{counts.get(tag.id, 0)}）")
            chip.setCheckable(True)
            chip.setChecked(tag.id == active_id)
            chip.setProperty("tagcolor", tag.color)
            chip.clicked.connect(lambda _c=False, tid=tag.id: self._on_chip(tid))
            chips.append(chip)

        # 每行 _PER_ROW 个，最后一行左对齐
        for i in range(0, len(chips), self._PER_ROW):
            row = QHBoxLayout()
            row.setSpacing(6)
            for chip in chips[i:i + self._PER_ROW]:
                row.addWidget(chip)
            row.addStretch(1)
            self.chips.addLayout(row)
        self.chips.addStretch(1)

    def _on_chip(self, tag_id: str) -> None:
        """再次点击当前筛选中的标签 = 取消筛选。"""
        if tag_id == self._active_id:
            self.cleared.emit()
        else:
            self.tag_selected.emit(tag_id)
