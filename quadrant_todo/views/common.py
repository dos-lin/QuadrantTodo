"""视图层共用工具。"""

from __future__ import annotations

from PySide6.QtWidgets import QLayout, QWidget


def clear_layout(layout: QLayout) -> None:
    """彻底清空布局，递归释放其中的控件、子布局与空白项。

    必须递归处理的原因：``layout.takeAt(i)`` 返回的项可能是控件、子布局或
    QSpacerItem，而常见的写法只判断 ``item.widget()``，会把子布局与 spacer
    直接丢弃 —— 子布局被 takeAt 后脱离父级却从未删除，成为孤儿对象并继续
    持有其下的控件；随后 Python GC 释放这些孤儿时，Qt 侧可能已被析构，
    从而产生悬垂指针（表现为退出期随机段错误）。

    此外统一用 ``setParent(None) + deleteLater()``，确保控件先从布局摘除、
    再由 Qt 在安全时机销毁，避免在事件分发过程中被就地析构。
    """
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child_layout = item.layout()
        if child_layout is not None:
            clear_layout(child_layout)
            # 子布局已被 takeAt 摘除，断开与父级的关联后交由 Qt 回收
            child_layout.setParent(None)
            child_layout.deleteLater()
            continue
        # QSpacerItem：无父级对象，直接释放
        spacer = item.spacerItem()
        if spacer is not None:
            spacer.changeSize(0, 0)
