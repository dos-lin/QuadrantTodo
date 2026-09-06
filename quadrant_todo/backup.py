"""数据备份（PRD F10.5 扩展：频率 / 时刻 / 保留份数可在设置中调整）。

实现要点：
- 用 sqlite3 官方 hot-backup API（``Connection.backup``）而非文件复制。运行期
  数据库处于 WAL 模式，直接 ``copy`` 会漏掉 WAL 中已提交但未 checkpoint 的数据；
  backup API 可在不关闭源连接的前提下拿到一致快照，因此定时备份与退出备份共用
  同一份实现。
- 备份文件按日期命名 ``data_YYYYMMDD.db``，同一天重复备份覆盖当天文件，
  于是「保留 N 份」等价于「保留最近 N 个有备份的日期」，频繁启停不会刷屏。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)


def _clamp(value: int, low: int, high: int) -> int:
    """把设置值夹回合法范围，避免脏数据导致备份数量失控。"""
    return max(low, min(value, high))


# ---------------------------------------------------------------- 设置读取

def backup_enabled(db) -> bool:
    return db.get_setting(config.KEY_BACKUP_ENABLED, config.DEFAULT_BACKUP_ENABLED) == "1"


def backup_interval_days(db) -> int:
    raw = db.get_int_setting(
        config.KEY_BACKUP_INTERVAL_DAYS, config.DEFAULT_BACKUP_INTERVAL_DAYS
    )
    return _clamp(raw, config.BACKUP_MIN_INTERVAL_DAYS, config.BACKUP_MAX_INTERVAL_DAYS)


def backup_keep_count(db) -> int:
    raw = db.get_int_setting(config.KEY_BACKUP_KEEP_COUNT, config.DEFAULT_BACKUP_KEEP_COUNT)
    return _clamp(raw, config.BACKUP_MIN_KEEP_COUNT, config.BACKUP_MAX_KEEP_COUNT)


def last_backup_at(db) -> datetime | None:
    raw = db.get_setting(config.KEY_LAST_BACKUP_AT, "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _backup_time_of_day(db) -> tuple[int, int]:
    """返回配置的备份时刻 (时, 分)；解析失败回退 20:00。"""
    raw = db.get_setting(config.KEY_BACKUP_TIME, config.DEFAULT_BACKUP_TIME)
    try:
        hour, minute, _second = (int(part) for part in raw.split(":"))
    except (ValueError, AttributeError):
        return 20, 0
    return hour % 24, minute % 60


# ---------------------------------------------------------------- 触发判定

def interval_elapsed(db, now: datetime) -> bool:
    """距上次备份是否已达到配置的间隔天数（只看间隔，不看当天时刻）。"""
    last = last_backup_at(db)
    if last is None:
        return True
    return now - last >= timedelta(days=backup_interval_days(db))


def is_due(db, now: datetime) -> bool:
    """定时点判定：已启用 + 间隔已到 + 已过当天备份时刻。"""
    if not backup_enabled(db):
        return False
    if not interval_elapsed(db, now):
        return False
    hour, minute = _backup_time_of_day(db)
    return (now.hour, now.minute) >= (hour, minute)


# ---------------------------------------------------------------- 执行

def prune(keep_count: int) -> None:
    """只保留最近 keep_count 份，其余按文件名（即日期）升序删除最旧的。"""
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = sorted(config.BACKUP_DIR.glob("data_*.db"))
    while len(backups) > keep_count:
        oldest = backups.pop(0)
        try:
            oldest.unlink(missing_ok=True)
        except OSError:
            logger.warning("旧备份删除失败：%s", oldest, exc_info=True)


def run(db, now: datetime | None = None) -> Path | None:
    """执行一次备份，返回备份文件路径；未执行或失败返回 None。

    源连接保持打开（热备份），因此可安全用于运行期的定时备份。
    """
    now = now or datetime.now()
    source = config.DB_PATH
    if not source.exists() or db.conn is None:
        return None
    try:
        config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        target = config.BACKUP_DIR / f"data_{now:%Y%m%d}.db"
        dst = sqlite3.connect(str(target))
        try:
            db.conn.backup(dst)
        finally:
            dst.close()
        prune(backup_keep_count(db))
        db.set_setting(config.KEY_LAST_BACKUP_AT, now.isoformat(timespec="seconds"))
        logger.info("数据已备份：%s", target.name)
        return target
    except (OSError, sqlite3.Error):
        logger.exception("备份失败")
        return None


def run_if_due(db, now: datetime | None = None) -> Path | None:
    """到点则备份（供 scheduler 每分钟轮询调用）。"""
    now = now or datetime.now()
    if not is_due(db, now):
        return None
    return run(db, now)


def run_if_interval_elapsed(db, now: datetime | None = None) -> Path | None:
    """间隔已到则备份（供退出时补偿，不要求已过当天时刻）。"""
    now = now or datetime.now()
    if not backup_enabled(db) or not interval_elapsed(db, now):
        return None
    return run(db, now)
