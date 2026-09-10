"""数据库 schema 与版本迁移（PRD 4.3 表结构 / 4.5 数据运维）。

版本通过 `PRAGMA user_version` 记录。后续结构变更时：
    1. SCHEMA_VERSION += 1
    2. 在 `_apply_migrations` 末尾按版本号追加分支
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 5

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS task (
    id              TEXT PRIMARY KEY,
    title           TEXT    NOT NULL,
    importance      INTEGER NOT NULL DEFAULT 0,
    due_date        TEXT,
    today_flag      INTEGER NOT NULL DEFAULT 0,
    locked_quadrant TEXT,
    estimate        INTEGER,
    status          TEXT    NOT NULL DEFAULT 'todo',
    reminder        TEXT,
    reminder_fired  INTEGER NOT NULL DEFAULT 0,
    note            TEXT,
    cycle           TEXT    NOT NULL DEFAULT 'none',
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL,
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_task_status ON task(status);
CREATE INDEX IF NOT EXISTS idx_task_due    ON task(due_date);
CREATE INDEX IF NOT EXISTS idx_task_today  ON task(today_flag);
CREATE INDEX IF NOT EXISTS idx_task_remind ON task(reminder, reminder_fired);

CREATE TABLE IF NOT EXISTS setting (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS behavior_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event      TEXT NOT NULL,
    params     TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_log_created ON behavior_log(created_at);

CREATE TABLE IF NOT EXISTS task_completion (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    completed_at  TEXT NOT NULL,
    cycle_seq     INTEGER NOT NULL DEFAULT 1,
    source        TEXT NOT NULL DEFAULT 'manual'
);
CREATE INDEX IF NOT EXISTS idx_compl_task ON task_completion(task_id);
CREATE INDEX IF NOT EXISTS idx_compl_at   ON task_completion(completed_at);

-- 子任务（依附父任务，无独立象限，PRD F20）
CREATE TABLE IF NOT EXISTS subtask (
    id           TEXT PRIMARY KEY,
    parent_id    TEXT NOT NULL,
    title        TEXT NOT NULL,
    done         INTEGER NOT NULL DEFAULT 0,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_subtask_parent ON subtask(parent_id);

-- 标签（与象限正交，PRD F21）
CREATE TABLE IF NOT EXISTS tag (
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL DEFAULT '#808080'
);

-- 任务-标签关联（多对多，PRD F21）
CREATE TABLE IF NOT EXISTS task_tag (
    task_id TEXT NOT NULL,
    tag_id  TEXT NOT NULL,
    PRIMARY KEY (task_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_tasktag_tag ON task_tag(tag_id);

-- 番茄钟会话（独立计时记录，不影响任务状态机，PRD F23）
CREATE TABLE IF NOT EXISTS pomodoro_session (
    id          TEXT PRIMARY KEY,
    task_id     TEXT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    planned_min INTEGER NOT NULL DEFAULT 25,
    actual_min  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'done'
);
CREATE INDEX IF NOT EXISTS idx_pomo_task  ON pomodoro_session(task_id);
CREATE INDEX IF NOT EXISTS idx_pomo_start ON pomodoro_session(started_at);

-- 小便签（脱离四象限，PRD F19；V1.0.1 新增 locked 字段；文章模块新增 top 置顶）
CREATE TABLE IF NOT EXISTS sticky_note (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    color       TEXT NOT NULL DEFAULT '#FFF9C4',
    pinned      INTEGER NOT NULL DEFAULT 0,
    locked      INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT,
    top         INTEGER NOT NULL DEFAULT 0
);

-- 文章（独立模块，不与任务/便签关联，2026-09-10 文章模块）
CREATE TABLE IF NOT EXISTS article (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '无标题文章',
    content     TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT
);

-- 文章-标签关联（复用 tag 表，2026-09-10 文章模块）
CREATE TABLE IF NOT EXISTS article_tag (
    article_id TEXT NOT NULL,
    tag_id     TEXT NOT NULL,
    PRIMARY KEY (article_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_articletag_tag ON article_tag(tag_id);
"""

#: v2.0 新增设置项的默认值（仅写入缺失项，不覆盖用户已设值）
_SETTING_DEFAULTS_V2 = {
    "theme": "system",
    "default_reminder_rule": "none",
    "default_reminder_offset": "60",
    "pomodoro_duration": "25",
}

#: v2.1 新增设置项的默认值
_SETTING_DEFAULTS_V3 = {
    "global_hotkey_enabled": "0",   # F25.3：默认关闭，安全优先
    "hotkey_toggle": "Ctrl+Alt+Q",  # F25.2：呼出/隐藏主窗口
    "hotkey_quickadd": "Ctrl+Alt+N",  # F25.2：全局快速添加
}


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def _apply_migrations(conn: sqlite3.Connection, from_version: int) -> None:
    """按版本递增执行迁移脚本。

    v1 为初始版本，v2 在一期 schema 基础上新增二期字段与完成历史表。
    所有 ALTER 都带 IF NOT EXISTS 语义：sqlite 不支持该子句，
    故此处仅在 from_version < 目标版本时执行（已升级的库不会重复执行）。
    """
    if from_version < 2:
        # Task 表新增二期字段（PRD 2.2 / 2.5）
        for stmt in (
            "ALTER TABLE task ADD COLUMN custom_rule TEXT",
            "ALTER TABLE task ADD COLUMN last_reset TEXT",
            "ALTER TABLE task ADD COLUMN reminder_rule TEXT NOT NULL DEFAULT 'none'",
            "ALTER TABLE task ADD COLUMN reminder_offset INTEGER",
        ):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                # 字段已存在（如开发期重复迁移），忽略
                pass

        # 新增设置默认值（仅写入缺失项）
        for key, value in _SETTING_DEFAULTS_V2.items():
            conn.execute(
                "INSERT OR IGNORE INTO setting (key, value) VALUES (?, ?)", (key, value)
            )

    if from_version < 3:
        # v2.1 新增 5 张数据表（PRD 2.3：subtask/tag/task_tag/pomodoro_session/sticky_note）。
        # 这些表在 v2.0 批次中因对应模块尚未实现而暂缓创建，此处补齐。
        for stmt in (
            "CREATE TABLE IF NOT EXISTS subtask ("
            " id TEXT PRIMARY KEY, parent_id TEXT NOT NULL, title TEXT NOT NULL,"
            " done INTEGER NOT NULL DEFAULT 0, sort_order INTEGER NOT NULL DEFAULT 0,"
            " created_at TEXT NOT NULL, completed_at TEXT)",
            "CREATE INDEX IF NOT EXISTS idx_subtask_parent ON subtask(parent_id)",
            "CREATE TABLE IF NOT EXISTS tag ("
            " id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, color TEXT NOT NULL DEFAULT '#808080')",
            "CREATE TABLE IF NOT EXISTS task_tag ("
            " task_id TEXT NOT NULL, tag_id TEXT NOT NULL, PRIMARY KEY (task_id, tag_id))",
            "CREATE INDEX IF NOT EXISTS idx_tasktag_tag ON task_tag(tag_id)",
            "CREATE TABLE IF NOT EXISTS pomodoro_session ("
            " id TEXT PRIMARY KEY, task_id TEXT, started_at TEXT NOT NULL, ended_at TEXT,"
            " planned_min INTEGER NOT NULL DEFAULT 25, actual_min INTEGER NOT NULL DEFAULT 0,"
            " status TEXT NOT NULL DEFAULT 'done')",
            "CREATE INDEX IF NOT EXISTS idx_pomo_task ON pomodoro_session(task_id)",
            "CREATE INDEX IF NOT EXISTS idx_pomo_start ON pomodoro_session(started_at)",
            "CREATE TABLE IF NOT EXISTS sticky_note ("
            " id TEXT PRIMARY KEY, content TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#FFF9C4',"
            " pinned INTEGER NOT NULL DEFAULT 0, sort_order INTEGER NOT NULL DEFAULT 0,"
            " created_at TEXT NOT NULL, updated_at TEXT)",
        ):
            conn.execute(stmt)

        for key, value in _SETTING_DEFAULTS_V3.items():
            conn.execute(
                "INSERT OR IGNORE INTO setting (key, value) VALUES (?, ?)", (key, value)
            )
        conn.commit()

    if from_version < 4:
        # V1.0.1：便签新增 locked 字段（锁定后禁止删除）
        # 旧库可能没有 locked 列；用 try/except 兼容重复迁移
        try:
            conn.execute("ALTER TABLE sticky_note ADD COLUMN locked INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            # 字段已存在（开发期重复迁移），忽略
            pass

    if from_version < 5:
        # 文章模块（2026-09-10）：sticky_note.top（列表置顶）+ article 表 + article_tag 关联表
        try:
            conn.execute("ALTER TABLE sticky_note ADD COLUMN top INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            # 字段已存在（开发期重复迁移），忽略
            pass
        for stmt in (
            "CREATE TABLE IF NOT EXISTS article ("
            " id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '无标题文章',"
            " content TEXT, created_at TEXT NOT NULL, updated_at TEXT)",
            "CREATE TABLE IF NOT EXISTS article_tag ("
            " article_id TEXT NOT NULL, tag_id TEXT NOT NULL,"
            " PRIMARY KEY (article_id, tag_id))",
            "CREATE INDEX IF NOT EXISTS idx_articletag_tag ON article_tag(tag_id)",
        ):
            conn.execute(stmt)
        conn.commit()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """建表并执行必要的版本迁移。"""
    conn.executescript(SCHEMA_SQL)
    version = current_version(conn)
    if version < SCHEMA_VERSION:
        _apply_migrations(conn, version)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
