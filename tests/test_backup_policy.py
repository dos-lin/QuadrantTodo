"""自动备份策略测试（PRD F10.5 扩展）。

覆盖：频率 / 时刻 / 保留份数三个设置项对备份行为的控制，
以及「同一天覆盖」「退出时补偿但近期不重复」两条关键规则。
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from quadrant_todo import backup, config
from quadrant_todo.db import Database


class BackupPolicyTest(unittest.TestCase):
    """备份策略：把 config 的路径指向临时目录，避免污染真实数据。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "data.db"
        self.backup_dir = self.root / "backups"
        self.backup_dir.mkdir()

        self.db = Database(db_path=self.db_path)
        self.db.connect()

        self._saved_paths = (config.DB_PATH, config.BACKUP_DIR)
        config.DB_PATH = self.db_path
        config.BACKUP_DIR = self.backup_dir

    def tearDown(self) -> None:
        config.DB_PATH, config.BACKUP_DIR = self._saved_paths
        self.db.close()
        self._tmp.cleanup()

    # ------------------------------------------------------------ 执行备份

    def test_run_creates_dated_backup_and_records_time(self) -> None:
        path = backup.run(self.db, datetime(2026, 9, 4, 21, 0, 0))
        self.assertIsNotNone(path, "首次备份应成功")
        self.assertEqual(path.name, "data_20260904.db")
        self.assertTrue(path.exists(), "备份文件应真实产生")
        self.assertEqual(
            self.db.get_setting(config.KEY_LAST_BACKUP_AT, ""),
            "2026-09-04T21:00:00",
            "备份后应记录上次备份时间，供间隔判断使用",
        )

    def test_same_day_backup_overwrites(self) -> None:
        """同一天重复备份覆盖当天文件——这是「保留 N 份 = 最近 N 天」的前提。"""
        backup.run(self.db, datetime(2026, 9, 4, 21, 0, 0))
        backup.run(self.db, datetime(2026, 9, 4, 23, 30, 0))
        names = sorted(p.name for p in self.backup_dir.glob("data_*.db"))
        self.assertEqual(names, ["data_20260904.db"], "同一天不应堆积多份备份")

    def test_backup_content_is_readable(self) -> None:
        """热备份产物必须是可用数据库，而不是空壳或损坏文件。"""
        self.db.set_setting("probe_key", "probe_value")
        path = backup.run(self.db, datetime(2026, 9, 4, 21, 0, 0))
        assert path is not None
        restored = Database(db_path=path)
        restored.connect()
        try:
            self.assertEqual(restored.get_setting("probe_key", ""), "probe_value")
        finally:
            restored.close()

    # ------------------------------------------------------------ 保留份数

    def test_prune_keeps_most_recent(self) -> None:
        for day in range(1, 6):
            (self.backup_dir / f"data_2026090{day}.db").write_bytes(b"")
        backup.prune(3)
        names = sorted(p.name for p in self.backup_dir.glob("data_*.db"))
        self.assertEqual(
            names,
            ["data_20260903.db", "data_20260904.db", "data_20260905.db"],
            "超出保留份数时应删最旧的日期",
        )

    def test_keep_count_setting_controls_prune(self) -> None:
        self.db.set_setting(config.KEY_BACKUP_KEEP_COUNT, 2)
        for day in range(1, 5):
            backup.run(self.db, datetime(2026, 9, day, 21, 0, 0))
        names = sorted(p.name for p in self.backup_dir.glob("data_*.db"))
        self.assertEqual(len(names), 2, "保留份数设置应生效")

    def test_keep_count_is_clamped(self) -> None:
        """脏数据不能让保留份数失控。"""
        self.db.set_setting(config.KEY_BACKUP_KEEP_COUNT, 9999)
        self.assertEqual(backup.backup_keep_count(self.db), config.BACKUP_MAX_KEEP_COUNT)
        self.db.set_setting(config.KEY_BACKUP_KEEP_COUNT, 0)
        self.assertEqual(backup.backup_keep_count(self.db), config.BACKUP_MIN_KEEP_COUNT)

    # ------------------------------------------------------------ 触发判定

    def test_is_due_requires_time_of_day(self) -> None:
        self.db.set_setting(config.KEY_BACKUP_TIME, "20:00:00")
        self.db.set_setting(config.KEY_BACKUP_INTERVAL_DAYS, 1)
        self.assertFalse(
            backup.is_due(self.db, datetime(2026, 9, 4, 19, 59, 0)),
            "未到备份时刻不应触发",
        )
        self.assertTrue(
            backup.is_due(self.db, datetime(2026, 9, 4, 20, 0, 0)),
            "到达备份时刻应触发",
        )

    def test_disabled_never_due(self) -> None:
        self.db.set_setting(config.KEY_BACKUP_ENABLED, "0")
        self.assertFalse(backup.is_due(self.db, datetime(2026, 9, 4, 23, 0, 0)))

    def test_interval_gate(self) -> None:
        self.db.set_setting(config.KEY_BACKUP_INTERVAL_DAYS, 3)
        self.db.set_setting(config.KEY_BACKUP_TIME, "20:00:00")
        self.db.set_setting(config.KEY_LAST_BACKUP_AT, "2026-09-03T20:00:00")
        self.assertFalse(
            backup.is_due(self.db, datetime(2026, 9, 4, 21, 0, 0)),
            "距上次备份不足 3 天不应触发",
        )
        self.assertTrue(
            backup.is_due(self.db, datetime(2026, 9, 6, 21, 0, 0)),
            "满 3 天且已过时刻应触发",
        )

    def test_first_run_is_due_without_history(self) -> None:
        """从未备份过 → 视为间隔已满，到时刻即备份。"""
        self.db.set_setting(config.KEY_BACKUP_TIME, "20:00:00")
        self.assertTrue(backup.is_due(self.db, datetime(2026, 9, 4, 20, 30, 0)))

    # ------------------------------------------------------------ 退出补偿

    def test_quit_compensates_when_interval_elapsed(self) -> None:
        """错过当天时刻时，退出只要间隔已满仍应补一次。"""
        self.db.set_setting(config.KEY_BACKUP_INTERVAL_DAYS, 1)
        self.db.set_setting(config.KEY_BACKUP_TIME, "23:59:00")
        self.db.set_setting(config.KEY_LAST_BACKUP_AT, "2026-09-02T20:00:00")
        path = backup.run_if_interval_elapsed(self.db, datetime(2026, 9, 4, 21, 0, 0))
        self.assertIsNotNone(path, "退出时距上次备份已满间隔，应补偿备份")

    def test_quit_skips_when_recently_backed_up(self) -> None:
        """刚备份过就退出，不应再产生一份——这正是原「每次退出都备份」的毛病。"""
        backup.run(self.db, datetime(2026, 9, 4, 20, 0, 0))
        path = backup.run_if_interval_elapsed(self.db, datetime(2026, 9, 4, 21, 0, 0))
        self.assertIsNone(path, "近期已备份，退出时不应重复备份")

    def test_quit_skips_when_disabled(self) -> None:
        self.db.set_setting(config.KEY_BACKUP_ENABLED, "0")
        path = backup.run_if_interval_elapsed(self.db, datetime(2026, 9, 4, 21, 0, 0))
        self.assertIsNone(path)


if __name__ == "__main__":
    unittest.main()
