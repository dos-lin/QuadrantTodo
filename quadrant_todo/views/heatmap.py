"""人生热力图视图（PRD F18）。

年历贡献图风格：每周一列、周日至周六七行，按每日完成数着色（五档）。
悬停显示当日完成数（F18.4），年份可切换（F18.5），顶部展示年度累计与连续打卡（F18.6）。
数据来自 db.daily_completion_counts(year) 与 db.heatmap_summary(year)。
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from .common import clear_layout

#: 五档着色的底色调（浅色/深色两套），与 styles.qss 配色呼应
_HEAT_LEVELS = {
    "light": ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"],
    "dark": ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"],
}


class HeatmapView(QWidget):
    """年度完成密度热力图。"""

    year_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.year = date.today().year
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        top = QHBoxLayout()
        self.prev_btn = QPushButton("‹ 上一年")
        self.prev_btn.setFlat(True)
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        top.addWidget(self.prev_btn)

        self.year_label = QLabel()
        self.year_label.setProperty("role", "view-title")
        self.year_label.setAlignment(Qt.AlignCenter)
        top.addWidget(self.year_label, 1)

        self.next_btn = QPushButton("下一年 ›")
        self.next_btn.setFlat(True)
        self.next_btn.clicked.connect(lambda: self._step(1))
        top.addWidget(self.next_btn)
        root.addLayout(top)

        self.summary = QLabel()
        self.summary.setProperty("role", "meta")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        # 图例（render 时按当前主题重建）
        self.legend_layout = QHBoxLayout()
        self.legend_layout.setSpacing(4)
        root.addLayout(self.legend_layout)

        # 网格区（逐月排布），用滚动区容纳内容高度
        self.scroll = QWidget()
        self.grid_layout = QVBoxLayout(self.scroll)
        self.grid_layout.setSpacing(2)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self.scroll)
        root.addWidget(area, 1)

    def _step(self, delta: int) -> None:
        self.year += delta
        self.year_changed.emit(self.year)

    def render(self, counts: dict[date, int], total: int, streak: int) -> None:
        palette = _HEAT_LEVELS.get(theme.current_scheme(), _HEAT_LEVELS["light"])
        self.year_label.setText(str(self.year))
        self._render_legend(palette)
        self._render_grid(counts, palette)

        self.summary.setText(
            f"累计完成 {total} 项"
            + (f" · 连续打卡 {streak} 天" if streak else " · 今日尚未完成")
        )

    def _render_legend(self, palette: list[str]) -> None:
        clear_layout(self.legend_layout)
        self.legend_layout.addWidget(QLabel("少"))
        for color in palette:
            sw = QLabel()
            sw.setFixedSize(12, 12)
            sw.setStyleSheet(f"background: {color}; border-radius: 2px;")
            self.legend_layout.addWidget(sw)
        self.legend_layout.addWidget(QLabel("多"))
        self.legend_layout.addStretch(1)

    def _render_grid(self, counts: dict[date, int], palette: list[str]) -> None:
        clear_layout(self.grid_layout)

        # 逐月排布，每月一列（含月份标题 + 该月每日色块），12 列均分横向空间
        month_row = QHBoxLayout()
        month_row.setSpacing(6)
        for month in range(1, 13):
            month_start = date(self.year, month, 1)
            if month == 12:
                next_m = date(self.year + 1, 1, 1)
            else:
                next_m = date(self.year, month + 1, 1)
            days = (next_m - month_start).days
            col = QVBoxLayout()
            col.setSpacing(2)
            col.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            cap = QLabel(f"{month}月")
            cap.setProperty("role", "meta")
            cap.setAlignment(Qt.AlignCenter)
            # 固定标题宽度：消除「10/11/12 月」比「1-9 月」宽导致的列宽不均
            cap.setFixedWidth(30)
            col.addWidget(cap)
            for d in range(days):
                day = month_start + timedelta(days=d)
                cnt = counts.get(day, 0)
                if cnt > 0:
                    lvl = 1 if cnt <= 2 else 2 if cnt <= 5 else 3 if cnt <= 9 else 4
                else:
                    lvl = 0
                cell = QLabel()
                cell.setFixedSize(13, 13)
                cell.setStyleSheet(f"background: {palette[lvl]}; border-radius: 2px;")
                cell.setToolTip(
                    f"{day.strftime('%Y-%m-%d')}：完成 {cnt} 项"
                    if cnt else f"{day.strftime('%Y-%m-%d')}：无完成"
                )
                cell.setAlignment(Qt.AlignCenter)
                col.addWidget(cell)
            month_row.addLayout(col, 1)
        self.grid_layout.addLayout(month_row)
