"""计时统计视图（PRD F23.5）。

展示今日 / 本周 / 累计专注时长与番茄数，以及各任务专注时长排行。
数据来自 db.pomodoro_stats()。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QWidget,
)


class StatsView(QWidget):
    """番茄钟计时统计页。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        self.summary = QLabel()
        self.summary.setProperty("role", "view-title")
        root.addWidget(self.summary)

        self.detail = QLabel()
        self.detail.setProperty("role", "meta")
        self.detail.setWordWrap(True)
        root.addWidget(self.detail)

        root.addStretch(1)

    def render(self, stats: dict, ranking: list[tuple[str, int]]) -> None:
        """stats: db.pomodoro_stats() 返回值；ranking: [(任务标题, 专注分钟), ...]。"""
        def hm(minutes: int) -> str:
            return f"{minutes // 60}小时{minutes % 60}分" if minutes >= 60 else f"{minutes}分钟"

        self.summary.setText(
            f"今日专注 {hm(stats['today_min'])}（{stats['today_cnt']} 个番茄）\n"
            f"本周专注 {hm(stats['week_min'])}（{stats['week_cnt']} 个番茄）\n"
            f"累计专注 {hm(stats['total_min'])}（{stats['total_cnt']} 个番茄）"
        )

        if ranking:
            lines = ["专注时长排行："]
            for i, (title, minutes) in enumerate(ranking[:10], 1):
                lines.append(f"{i}. {title} — {hm(minutes)}")
            self.detail.setText("\n".join(lines))
        else:
            self.detail.setText("暂无计时记录。在任务详情面板点击「开始番茄钟」开始专注。")
