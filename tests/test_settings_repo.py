"""设置页 GitHub 仓库地址（2026-09-03）：展示 + 跳转 + 复制，以及选中态 accent 边框 QSS 回归。"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QLabel, QTabWidget

from quadrant_todo.views.settings import REPO_URL, SettingsDialog


class SettingsRepoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _make_dialog(self) -> SettingsDialog:
        return SettingsDialog(7, 3)

    def test_repo_link_present(self) -> None:
        dlg = self._make_dialog()
        links = [w for w in dlg.findChildren(QLabel) if REPO_URL in (w.text() or "")]
        self.assertTrue(links, "设置对话框应展示 GitHub 仓库链接")
        self.assertIn("dos-lin/QuadrantTodo", links[0].text())

    def test_open_repo_invokes_desktop_services(self) -> None:
        dlg = self._make_dialog()
        with patch("quadrant_todo.views.settings.QDesktopServices.openUrl") as mock_open:
            dlg._open_repo()
            mock_open.assert_called_once()
            arg = mock_open.call_args[0][0]
            self.assertIsInstance(arg, QUrl)
            self.assertEqual(arg.toString(), REPO_URL)

    def test_copy_repo_writes_clipboard(self) -> None:
        dlg = self._make_dialog()
        fake = MagicMock()
        with patch(
            "quadrant_todo.views.settings.QGuiApplication.clipboard",
            return_value=fake,
        ):
            dlg._copy_repo()
            fake.setText.assert_called_once_with(REPO_URL)

    def test_selected_qss_has_accent_border(self) -> None:
        base = Path(__file__).resolve().parents[1] / "quadrant_todo"
        light_text = (base / "styles.qss").read_text(encoding="utf-8")
        dark_text = (base / "styles" / "dark.qss").read_text(encoding="utf-8")
        self.assertIn('TaskItemWidget[selected="true"]', light_text)
        # 选中态必须有左侧 accent 边框（区分未选中），且默认透明同宽无位移
        self.assertIn("border-left-color", light_text)
        self.assertIn("border-left-color", dark_text)

    def test_settings_uses_tabs(self) -> None:
        dlg = self._make_dialog()
        tabs = dlg.findChildren(QTabWidget)
        self.assertEqual(len(tabs), 1, "设置对话框应使用 QTabWidget")
        tab = tabs[0]
        labels = [tab.tabText(i) for i in range(tab.count())]
        self.assertEqual(labels, ["通用", "热键", "数据", "关于"])


if __name__ == "__main__":
    unittest.main()
