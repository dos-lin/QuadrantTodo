"""截止日期日历默认日期回归测试（offscreen 平台，确定性）。

PRD F5.10 修正：任务未设置截止日期时，打开日历应默认选中「今天」，
而非落到最小日期（1752 年）。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QEvent
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QApplication, QDateEdit

from quadrant_todo.views.detail import _DueCalendarWidget


def main() -> int:
    app = QApplication([])

    # 复刻详情面板的 due_edit 配置
    de = QDateEdit()
    de.setMinimumDate(QDateEdit().minimumDate())
    de.setSpecialValueText("未设置")

    de.setDate(de.minimumDate())  # 未设置状态
    cal = _DueCalendarWidget(de)
    cal.showEvent(QShowEvent())
    assert cal.selectedDate() == QDate.currentDate(), (
        f"未设置时日历应默认选中今天，实际 {cal.selectedDate().toString('yyyy-MM-dd')}"
    )

    # 已设置具体日期时，弹窗不应被今天覆盖
    # （模拟 QDateEdit 在弹窗前将选中日期同步为当前值）
    some = QDate(2026, 9, 15)
    de.setDate(some)
    cal.setSelectedDate(some)
    cal.showEvent(QShowEvent())
    assert cal.selectedDate() == some, (
        f"已设置日期时不应被今天覆盖，实际 {cal.selectedDate().toString('yyyy-MM-dd')}"
    )

    print("test_due_calendar OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
