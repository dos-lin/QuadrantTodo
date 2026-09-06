"""F10.2 数据文件损坏自动重建回归测试。"""
import sqlite3
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

# 确保 offscreen 下存在 QApplication（控件构造需要）
_app = QApplication.instance() or QApplication([])

from quadrant_todo.db import Database


def _write_garbage(path: Path) -> None:
    path.write_bytes(b"\x00\x01\x02not a sqlite database file at all\xfe\xff")


def test_corrupt_db_recovers_and_rebuilds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "data.db"
        _write_garbage(db_path)

        db = Database(db_path)
        conn = db.connect()

        # 重建后连接可用：能建表、能写入读取
        assert db.recovered is True
        assert db.recovered_backup is not None
        backup = Path(db.recovered_backup)
        assert backup.exists()
        assert "corrupt" in backup.name

        task = __import__("quadrant_todo.models", fromlist=["Task"]).Task(
            title="恢复后任务"
        )
        db.save_task(task)
        loaded = db.get_task(task.id)
        assert loaded is not None
        assert loaded.title == "恢复后任务"

        conn.close()


def test_healthy_db_does_not_recover() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "data.db"
        db = Database(db_path)
        db.connect()
        assert db.recovered is False
        assert db.recovered_backup is None
        db.close()


def test_corrupt_db_with_wal_residue_cleaned() -> None:
    """损坏主文件旁残留 -wal/-shm 时应被清理，避免新库误读。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "data.db"
        _write_garbage(db_path)
        (db_path.with_name(db_path.name + "-wal")).write_bytes(b"wal")
        (db_path.with_name(db_path.name + "-shm")).write_bytes(b"shm")

        # 直接验证恢复逻辑（不触发重建，避免新库重新生成 -wal）
        db = Database(db_path)
        db.conn = None
        db._recover_corrupt_database()

        assert db.recovered_backup is not None
        assert Path(db.recovered_backup).exists()
        assert not db_path.exists()  # 原文件已移走
        assert not (db_path.with_name(db_path.name + "-wal")).exists()
        assert not (db_path.with_name(db_path.name + "-shm")).exists()
