"""数据文件位置：便携默认 + ini 持久化 + 迁移（F10.1 变更）。"""
import os
from pathlib import Path

import pytest

from quadrant_todo import config


def test_env_override_active() -> None:
    """conftest 设置了 QUADRANT_DATA_DIR，config 应优先采用（测试隔离）。"""
    assert "QUADRANT_DATA_DIR" in os.environ
    assert config.DATA_DIR == Path(os.environ["QUADRANT_DATA_DIR"])


def test_save_and_read_ini(tmp_path, monkeypatch) -> None:
    ini = tmp_path / "app_config.ini"
    monkeypatch.setattr(config, "_CONFIG_INI", ini)
    new_dir = tmp_path / "newdata"
    config.save_data_dir(new_dir)
    assert ini.exists()
    assert config._read_ini_data_dir() == new_dir.resolve()


def test_migrate_data_to(tmp_path, monkeypatch) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "data.db").write_text("x")
    (src / "data.db-wal").write_text("w")
    (src / "backups").mkdir()
    (src / "backups" / "b.db").write_text("y")
    (src / "logs").mkdir()
    (src / "logs" / "l.log").write_text("z")

    monkeypatch.setattr(config, "DATA_DIR", src)
    dst = tmp_path / "dst"
    moved = config.migrate_data_to(dst)

    assert "data.db" in moved and "backups" in moved and "logs" in moved
    assert (dst / "data.db").exists() and not (src / "data.db").exists()
    assert (dst / "backups" / "b.db").exists()
    assert (dst / "logs" / "l.log").exists()
    assert (dst / "data.db-wal").exists()


def test_migrate_skips_existing_target(tmp_path, monkeypatch) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "data.db").write_text("old")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "data.db").write_text("keep")

    monkeypatch.setattr(config, "DATA_DIR", src)
    moved = config.migrate_data_to(dst)

    assert "data.db" not in moved
    assert (dst / "data.db").read_text() == "keep"  # 不被覆盖
    assert (src / "data.db").exists()                # 旧文件保留
