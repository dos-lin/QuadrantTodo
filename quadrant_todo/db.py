"""SQLite 数据访问层（PRD 4.3 / 4.5）。

只做数据读写，不含任何业务规则 —— 象限判定一律走 quadrant.py 的纯函数。
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from . import config, migration
from .models import (
    STATUS_DONE,
    Article,
    PomodoroSession,
    StickyNote,
    Subtask,
    Tag,
    Task,
    TaskCompletion,
)
from .recurrence import compute_relative_reminder, defer_due_date, next_boundary

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """数据库写入失败（已重试后仍失败）。"""


class Database:
    """任务数据库。所有写操作失败时重试一次（PRD 4.5）。"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else config.DB_PATH
        self.conn: Optional[sqlite3.Connection] = None
        self.recovered = False                  # F10.2：本次连接是否经历过损坏重建
        self.recovered_backup: Optional[str] = None  # 损坏文件备份路径

    # ------------------------------------------------------------ 连接

    def connect(self) -> sqlite3.Connection:
        config.ensure_dirs()
        try:
            self.conn = self._open_and_migrate()
            return self.conn
        except sqlite3.DatabaseError:
            # 数据文件可能已损坏（PRD F10.2）：备份原文件后重建空库
            logger.exception("数据文件可能已损坏，尝试自动重建")
            self._recover_corrupt_database()
            self.conn = self._open_and_migrate()
            self.recovered = True
            return self.conn

    def _open_and_migrate(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")   # PRD F7.3
            conn.execute("PRAGMA foreign_keys = ON")
            migration.ensure_schema(conn)
        except sqlite3.Error:
            # 异常时关闭局部连接释放文件锁，否则损坏文件在 Windows 下无法被移走/删除
            conn.close()
            raise
        return conn

    def _recover_corrupt_database(self) -> None:
        """F10.2：将损坏文件重命名为 .corrupt.<时间戳> 备份，并清理 WAL/SHM 残留。

        备份失败时退而直接删除原文件，保证应用至少能启动。
        """
        try:
            if self.conn is not None:
                try:
                    self.conn.close()
                except sqlite3.Error:
                    pass
                self.conn = None
            # 清理 WAL/SHM 残留，避免新库误读旧日志
            for suffix in ("-wal", "-shm"):
                extra = self.db_path.with_name(self.db_path.name + suffix)
                if extra.exists():
                    try:
                        extra.unlink()
                    except OSError as exc:
                        logger.warning("清理残留文件失败 %s: %s", extra, exc)
            if self.db_path.exists():
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup = self.db_path.with_name(
                    f"{self.db_path.stem}.corrupt.{stamp}{self.db_path.suffix}"
                )
                shutil.move(str(self.db_path), str(backup))
                self.recovered_backup = str(backup)
                logger.warning("数据文件已损坏，原文件备份至 %s 并重建空库", backup)
        except OSError as exc:
            logger.error("损坏文件备份失败，将尝试直接重建：%s", exc)
            try:
                if self.db_path.exists():
                    self.db_path.unlink()
            except OSError:
                pass

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def _write(self, sql: str, params=()) -> sqlite3.Cursor:
        """执行写操作，失败重试一次（PRD 4.5 写失败处理）。

        params 支持元组（qmark 占位）或字典（命名占位）。
        """
        assert self.conn is not None, "数据库未连接"
        bound = params if isinstance(params, dict) else tuple(params)
        try:
            cur = self.conn.execute(sql, bound)
            self.conn.commit()
            return cur
        except sqlite3.Error as exc:
            logger.warning("数据库写入失败，重试一次: %s", exc)
            try:
                cur = self.conn.execute(sql, bound)
                self.conn.commit()
                return cur
            except sqlite3.Error as retry_exc:
                logger.error("数据库写入最终失败: %s", retry_exc)
                raise DatabaseError(str(retry_exc)) from retry_exc

    # ------------------------------------------------------------ 任务 CRUD

    def save_task(self, task: Task) -> None:
        """插入或更新任务。"""
        row = task.to_row()
        columns = ", ".join(row)
        placeholders = ", ".join(f":{key}" for key in row)
        self._write(f"INSERT OR REPLACE INTO task ({columns}) VALUES ({placeholders})", row)

    def get_task(self, task_id: str) -> Optional[Task]:
        assert self.conn is not None
        cur = self.conn.execute("SELECT * FROM task WHERE id = ?", (task_id,))
        row = cur.fetchone()
        return Task.from_row(row) if row else None

    def all_tasks(self) -> list[Task]:
        assert self.conn is not None
        cur = self.conn.execute("SELECT * FROM task ORDER BY sort_order, created_at")
        return [Task.from_row(row) for row in cur.fetchall()]

    def delete_task(self, task_id: str) -> None:
        """删除任务并级联清理关联数据。

        PRD F20.4 父任务删除级联子任务；F21.5 任务删除级联 task_tag 关联。
        注意：task_completion 与 pomodoro_session 为历史记录，保留不删。
        """
        self._write("DELETE FROM task WHERE id = ?", (task_id,))
        self._write("DELETE FROM subtask WHERE parent_id = ?", (task_id,))
        self._write("DELETE FROM task_tag WHERE task_id = ?", (task_id,))

    def next_sort_order(self) -> int:
        """新建任务的排序位：当前最大值 + 1（PRD F1.11 追加到末尾）。"""
        assert self.conn is not None
        cur = self.conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM task")
        return int(cur.fetchone()[0]) + 1

    def reset_today_flags(self) -> None:
        """跨日时清空所有「加入今日」标记（PRD F4.7 / F12.2）。"""
        self._write("UPDATE task SET today_flag = 0 WHERE today_flag = 1")

    def mark_reminder_fired(self, task_id: str) -> None:
        """标记提醒已触发，避免重复打扰（PRD F9.6）。"""
        self._write("UPDATE task SET reminder_fired = 1 WHERE id = ?", (task_id,))

    def pending_reminders(self, now: datetime) -> list[Task]:
        """取出到点且未触发的提醒（PRD F9.1）。"""
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT * FROM task WHERE reminder IS NOT NULL AND reminder_fired = 0 "
            "AND reminder <= ? AND status NOT IN ('done', 'abandoned')",
            (now.isoformat(),),
        )
        return [Task.from_row(row) for row in cur.fetchall()]

    def set_status(self, task_id: str, status: str, completed_at: Optional[datetime] = None) -> None:
        self._write(
            "UPDATE task SET status = ?, completed_at = ? WHERE id = ?",
            (status, completed_at.isoformat() if completed_at else None, task_id),
        )

    def clear_tasks(self) -> None:
        """清空任务表，用于数据导入（PRD F10.6）。"""
        self._write("DELETE FROM task")

    def count_done_in_range(self, start: datetime, end: datetime) -> int:
        """统计区间内完成的任务数（PRD F6.2 分子）。"""
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM task WHERE status = ? AND completed_at IS NOT NULL "
            "AND completed_at >= ? AND completed_at < ?",
            (STATUS_DONE, start.isoformat(), end.isoformat()),
        )
        return int(cur.fetchone()[0])

    def count_planned_in_range(self, start: datetime, end: datetime) -> int:
        """统计区间内的计划总数（PRD F6.2 分母）。

        口径：dueDate 落在周期内、或 createdAt 落在周期内的任务中，dueDate 不为空的任务
        （去重），不含已放弃任务。无截止日期的任务不计入。
        """
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM task WHERE due_date IS NOT NULL AND status != 'abandoned' "
            "AND ((due_date >= ? AND due_date < ?) OR (created_at >= ? AND created_at < ?))",
            (start.date().isoformat(), end.date().isoformat(),
             start.isoformat(), end.isoformat()),
        )
        return int(cur.fetchone()[0])

    def count_unscheduled(self) -> int:
        """无截止日期且未完成的任务数（PRD F6.5 待安排）。"""
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM task WHERE due_date IS NULL AND status NOT IN ('done', 'abandoned')"
        )
        return int(cur.fetchone()[0])

    # ------------------------------------------------------------ 周期任务重生（PRD F16.3）

    def execute_cycle_resets(self, today: date) -> int:
        """对到期需重生的周期任务执行「原地复活 + 边界重置」。

        返回成功重生的任务数。需在跨日检测与启动时调用（见 scheduler / main）。
        每次重生写入 `task_completion` 已由「标记完成时记录」承担，此处仅做状态重置。
        """
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT * FROM task WHERE cycle != 'none' AND status = 'done'"
        )
        resets = 0
        for row in cur.fetchall():
            task = Task.from_row(row)
            anchor = task.last_reset or (
                task.completed_at.date() if task.completed_at else task.created_at.date()
            )
            boundary = next_boundary(anchor, task.cycle, task.custom_rule)
            if today < boundary:
                continue

            task.status = "todo"
            task.completed_at = None
            task.today_flag = False
            task.last_reset = today
            task.due_date = defer_due_date(task.due_date, task.cycle, task.custom_rule)
            if task.reminder_rule == "relative":
                task.reminder = compute_relative_reminder(task.due_date, task.reminder_offset)
            self.save_task(task)
            resets += 1
        return resets

    # ------------------------------------------------------------ 完成历史（PRD 2.3）

    def record_completion(self, completion: TaskCompletion) -> None:
        """写入一条不可变完成记录（热力图 / 周期计数 / 已完成分组共用）。"""
        row = completion.to_row()
        columns = ", ".join(row)
        placeholders = ", ".join(f":{key}" for key in row)
        self._write(
            f"INSERT OR REPLACE INTO task_completion ({columns}) VALUES ({placeholders})", row
        )

    def count_completions(self, task_id: str) -> int:
        """某任务已有完成记录数，用于推算本次 cycle_seq（PRD F16.7）。"""
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM task_completion WHERE task_id = ?", (task_id,)
        )
        return int(cur.fetchone()[0])

    def get_completions_in_range(self, start: date, end: date) -> list[dict]:
        """返回 [start, end) 区间内的完成记录（含任务标题，已删任务跳过）。"""
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT c.task_id, c.completed_at, c.cycle_seq, t.title "
            "FROM task_completion c LEFT JOIN task t ON t.id = c.task_id "
            "WHERE c.completed_at >= ? AND c.completed_at < ? AND t.title IS NOT NULL "
            "ORDER BY c.completed_at",
            (start.isoformat(), end.isoformat()),
        )
        return [
            {"task_id": r["task_id"], "completed_at": r["completed_at"], "cycle_seq": r["cycle_seq"], "title": r["title"]}
            for r in cur.fetchall()
        ]

    # ------------------------------------------------------------ 时段 / 日历视图查询

    def tasks_in_period(self, kind: str, anchor: date) -> list[Task]:
        """周/月/年待办：dueDate 落在时段内且未关闭的任务（PRD F15.2）。"""
        from .recurrence import period_range

        start, end = period_range(kind, anchor)
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT * FROM task WHERE status NOT IN ('done', 'abandoned') "
            "AND due_date IS NOT NULL AND due_date >= ? AND due_date < ? "
            "ORDER BY due_date, sort_order",
            (start.isoformat(), end.isoformat()),
        )
        return [Task.from_row(r) for r in cur.fetchall()]

    def tasks_by_due_date(self, target: date) -> list[Task]:
        """日历视图：某日截止的未关闭任务（PRD F17.2）。"""
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT * FROM task WHERE status NOT IN ('done', 'abandoned') "
            "AND due_date = ? ORDER BY importance DESC, sort_order",
            (target.isoformat(),),
        )
        return [Task.from_row(r) for r in cur.fetchall()]

    # ------------------------------------------------------------ 子任务（PRD F20）

    def save_subtask(self, subtask: "Subtask") -> None:
        """插入或更新子任务。"""
        row = subtask.to_row()
        columns = ", ".join(row)
        placeholders = ", ".join(f":{key}" for key in row)
        self._write(
            f"INSERT OR REPLACE INTO subtask ({columns}) VALUES ({placeholders})", row
        )

    def get_subtasks(self, parent_id: str) -> list["Subtask"]:
        """父任务下的全部子任务，按 sort_order 排序（PRD F20）。"""
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT * FROM subtask WHERE parent_id = ? ORDER BY sort_order, created_at",
            (parent_id,),
        )
        return [Subtask.from_row(r) for r in cur.fetchall()]

    def delete_subtask(self, subtask_id: str) -> None:
        self._write("DELETE FROM subtask WHERE id = ?", (subtask_id,))

    def delete_subtasks_by_parent(self, parent_id: str) -> None:
        """父任务删除时级联删除（PRD F20.4）。"""
        self._write("DELETE FROM subtask WHERE parent_id = ?", (parent_id,))

    def get_incomplete_subtask_count(self, task_id: str) -> int:
        """返回父任务下未完成的子任务数（子任务约束：须全部完成才能完成主任务）。"""
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM subtask WHERE parent_id = ? AND done = 0", (task_id,)
        )
        return int(cur.fetchone()[0] or 0)

    # ------------------------------------------------------------ 标签（PRD F21）

    def create_tag(self, name: str, color: str = "#808080") -> Optional["Tag"]:
        """新建标签；重名时返回 None（PRD F21.1 唯一约束）。"""
        assert self.conn is not None
        existing = self.find_tag_by_name(name)
        if existing is not None:
            return None
        tag_id = uuid.uuid4().hex
        self._write(
            "INSERT INTO tag (id, name, color) VALUES (?, ?, ?)",
            (tag_id, name, color),
        )
        return Tag(id=tag_id, name=name, color=color)

    def find_tag_by_name(self, name: str) -> Optional["Tag"]:
        assert self.conn is not None
        cur = self.conn.execute("SELECT * FROM tag WHERE name = ?", (name,))
        row = cur.fetchone()
        return Tag.from_row(row) if row else None

    def get_tags(self) -> list["Tag"]:
        """全部标签（PRD F21.6：保留无关联任务的标签）。"""
        assert self.conn is not None
        cur = self.conn.execute("SELECT * FROM tag ORDER BY name")
        return [Tag.from_row(r) for r in cur.fetchall()]

    def delete_tag(self, tag_id: str) -> None:
        """删除标签（不影响任务本体），并清理关联（PRD F21.6）。"""
        self._write("DELETE FROM task_tag WHERE tag_id = ?", (tag_id,))
        self._write("DELETE FROM tag WHERE id = ?", (tag_id,))

    def get_task_tag_ids(self, task_id: str) -> list[str]:
        assert self.conn is not None
        cur = self.conn.execute("SELECT tag_id FROM task_tag WHERE task_id = ?", (task_id,))
        return [r["tag_id"] for r in cur.fetchall()]

    def set_task_tags(self, task_id: str, tag_ids: list[str]) -> None:
        """用给定标签集合整体替换任务的标签关联（PRD F21.5）。"""
        assert self.conn is not None
        self._write("DELETE FROM task_tag WHERE task_id = ?", (task_id,))
        for tag_id in tag_ids:
            self._write(
                "INSERT OR IGNORE INTO task_tag (task_id, tag_id) VALUES (?, ?)",
                (task_id, tag_id),
            )

    def tasks_with_all_tags(self, tag_ids: list[str]) -> list[str]:
        """返回同时拥有全部给定标签的任务 id（多标签 AND 筛选，PRD F21.4）。"""
        if not tag_ids:
            return []
        assert self.conn is not None
        placeholders = ", ".join("?" for _ in tag_ids)
        cur = self.conn.execute(
            f"SELECT task_id FROM task_tag WHERE tag_id IN ({placeholders}) "
            f"GROUP BY task_id HAVING COUNT(DISTINCT tag_id) = ?",
            (*tag_ids, len(tag_ids)),
        )
        return [r["task_id"] for r in cur.fetchall()]

    # ------------------------------------------------------------ 小便签（PRD F19）

    def save_sticky(self, note: "StickyNote") -> None:
        row = note.to_row()
        columns = ", ".join(row)
        placeholders = ", ".join(f":{key}" for key in row)
        self._write(
            f"INSERT OR REPLACE INTO sticky_note ({columns}) VALUES ({placeholders})", row
        )

    def get_stickies(self) -> list["StickyNote"]:
        assert self.conn is not None
        # 文章模块：置顶的便签（top=1）排在普通便签之前
        cur = self.conn.execute(
            "SELECT * FROM sticky_note ORDER BY top DESC, sort_order, created_at"
        )
        return [StickyNote.from_row(r) for r in cur.fetchall()]

    def get_pinned_stickies(self) -> list["StickyNote"]:
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT * FROM sticky_note WHERE pinned = 1 ORDER BY sort_order, created_at"
        )
        return [StickyNote.from_row(r) for r in cur.fetchall()]

    def delete_sticky(self, note_id: str) -> None:
        self._write("DELETE FROM sticky_note WHERE id = ?", (note_id,))

    def clear_stickies(self) -> None:
        self._write("DELETE FROM sticky_note")

    # ------------------------------------------------------------ 文章（文章模块，2026-09-10）

    def save_article(self, article: "Article") -> None:
        """插入或更新文章；每次保存刷新 updated_at。"""
        article.updated_at = datetime.now()
        row = article.to_row()
        columns = ", ".join(row)
        placeholders = ", ".join(f":{key}" for key in row)
        self._write(
            f"INSERT OR REPLACE INTO article ({columns}) VALUES ({placeholders})", row
        )

    def get_articles(self) -> list["Article"]:
        """全部文章，按 updated_at 倒序（最近编辑的在前）。"""
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT * FROM article ORDER BY updated_at DESC, created_at DESC"
        )
        return [Article.from_row(r) for r in cur.fetchall()]

    def get_article(self, article_id: str) -> Optional["Article"]:
        assert self.conn is not None
        cur = self.conn.execute("SELECT * FROM article WHERE id = ?", (article_id,))
        row = cur.fetchone()
        return Article.from_row(row) if row else None

    def delete_article(self, article_id: str) -> None:
        """删除文章及其标签关联。"""
        self._write("DELETE FROM article_tag WHERE article_id = ?", (article_id,))
        self._write("DELETE FROM article WHERE id = ?", (article_id,))

    # 搜索语法前缀 -> 作用域（按长度降序无必要，首个匹配即为最前前缀）
    _SEARCH_SCOPES = (("title:", "title"), ("content:", "content"), ("tag:", "tag"))

    def parse_search_query(self, keyword: str) -> tuple[str, str]:
        """解析文章搜索语法，返回 (scope, term)。

        scope ∈ {"all","title","content","tag"}：
        - 无前缀：同时搜索标题 / 正文 / 标签
        - `title:xxx` 只搜标题；`content:xxx` 只搜正文；`tag:xxx` 只搜标签
        term 为去掉前缀并裁掉首尾空白后的查询词（用于列表高亮）。
        前缀匹配不区分大小写。
        """
        if not keyword:
            return "all", ""
        kw = keyword.strip()
        low = kw.lower()
        for prefix, scope in self._SEARCH_SCOPES:
            if low.startswith(prefix):
                return scope, kw[len(prefix):].strip()
        return "all", kw

    def search_articles(self, keyword: str) -> list["Article"]:
        """按搜索语法在标题 / 正文 / 标签中模糊匹配文章。

        支持 `title:` / `content:` / `tag:` 前缀限定范围；无前缀则三者都搜。
        空查询词视为未筛选（返回全部）。
        """
        assert self.conn is not None
        scope, term = self.parse_search_query(keyword)
        if not term:
            return self.get_articles()
        kw = f"%{term}%"
        if scope == "title":
            cur = self.conn.execute(
                "SELECT * FROM article WHERE title LIKE ? "
                "ORDER BY updated_at DESC, created_at DESC",
                (kw,),
            )
        elif scope == "content":
            cur = self.conn.execute(
                "SELECT * FROM article WHERE content LIKE ? "
                "ORDER BY updated_at DESC, created_at DESC",
                (kw,),
            )
        elif scope == "tag":
            cur = self.conn.execute(
                "SELECT DISTINCT a.* FROM article a "
                "JOIN article_tag at ON at.article_id = a.id "
                "JOIN tag t ON t.id = at.tag_id "
                "WHERE t.name LIKE ? ORDER BY a.updated_at DESC, a.created_at DESC",
                (kw,),
            )
        else:  # all
            cur = self.conn.execute(
                "SELECT DISTINCT a.* FROM article a "
                "LEFT JOIN article_tag at ON at.article_id = a.id "
                "LEFT JOIN tag t ON t.id = at.tag_id "
                "WHERE a.title LIKE ? OR a.content LIKE ? OR t.name LIKE ? "
                "ORDER BY a.updated_at DESC, a.created_at DESC",
                (kw, kw, kw),
            )
        return [Article.from_row(r) for r in cur.fetchall()]

    def get_article_tag_ids(self, article_id: str) -> list[str]:
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT tag_id FROM article_tag WHERE article_id = ?", (article_id,)
        )
        return [r["tag_id"] for r in cur.fetchall()]

    def get_article_tag_names(self, article_id: str) -> list[str]:
        """文章导出的标签名列表（按名称排序）。"""
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT t.name FROM article_tag at "
            "JOIN tag t ON t.id = at.tag_id "
            "WHERE at.article_id = ? ORDER BY t.name",
            (article_id,),
        )
        return [r["name"] for r in cur.fetchall()]

    def set_article_tags(self, article_id: str, tag_ids: list[str]) -> None:
        """用给定标签集合整体替换文章的标签关联。"""
        assert self.conn is not None
        self._write("DELETE FROM article_tag WHERE article_id = ?", (article_id,))
        for tag_id in tag_ids:
            self._write(
                "INSERT OR IGNORE INTO article_tag (article_id, tag_id) VALUES (?, ?)",
                (article_id, tag_id),
            )

    # ------------------------------------------------------------ 番茄钟会话（PRD F23）

    def save_pomodoro_session(self, session: "PomodoroSession") -> None:
        row = session.to_row()
        columns = ", ".join(row)
        placeholders = ", ".join(f":{key}" for key in row)
        self._write(
            f"INSERT OR REPLACE INTO pomodoro_session ({columns}) VALUES ({placeholders})", row
        )

    def pomodoro_stats(self, now: datetime | None = None) -> dict:
        """计时汇总（PRD F23.5）。返回今日/本周/累计专注分钟、番茄数、各任务排行。"""
        assert self.conn is not None
        now = now or datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = (today_start - timedelta(days=today_start.weekday()))

        def _sum_since(start: datetime) -> tuple[int, int]:
            cur = self.conn.execute(
                "SELECT COALESCE(SUM(actual_min), 0), COUNT(*) FROM pomodoro_session "
                "WHERE started_at >= ?",
                (start.isoformat(),),
            )
            mins, cnt = cur.fetchone()
            return int(mins or 0), int(cnt or 0)

        today_min, today_cnt = _sum_since(today_start)
        week_min, week_cnt = _sum_since(week_start)
        cur = self.conn.execute(
            "SELECT COALESCE(SUM(actual_min), 0), COUNT(*) FROM pomodoro_session"
        )
        total_min, total_cnt = cur.fetchone()

        # 各任务专注时长排行（已完成会话，actual_min>0）
        cur = self.conn.execute(
            "SELECT task_id, COALESCE(SUM(actual_min), 0) AS m FROM pomodoro_session "
            "WHERE task_id IS NOT NULL GROUP BY task_id ORDER BY m DESC LIMIT 10"
        )
        ranking = [(r["task_id"], int(r["m"])) for r in cur.fetchall()]
        return {
            "today_min": today_min,
            "today_cnt": today_cnt,
            "week_min": week_min,
            "week_cnt": week_cnt,
            "total_min": int(total_min or 0),
            "total_cnt": int(total_cnt or 0),
            "ranking": ranking,
        }

    def task_focus_minutes(self, task_id: str) -> int:
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT COALESCE(SUM(actual_min), 0) FROM pomodoro_session WHERE task_id = ?",
            (task_id,),
        )
        return int(cur.fetchone()[0] or 0)

    def task_pomodoro_count(self, task_id: str) -> int:
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM pomodoro_session WHERE task_id = ?", (task_id,)
        )
        return int(cur.fetchone()[0])

    # ------------------------------------------------------------ 任务搜索（PRD F22）

    def search_tasks(self, keyword: str, include_closed: bool = False) -> list[Task]:
        """按标题+备注模糊匹配（PRD F22.1）。include_closed 默认否（F22.4）。"""
        assert self.conn is not None
        kw = f"%{keyword}%"
        if include_closed:
            cur = self.conn.execute(
                "SELECT * FROM task WHERE title LIKE ? OR note LIKE ? "
                "ORDER BY sort_order, created_at",
                (kw, kw),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM task WHERE (title LIKE ? OR note LIKE ?) "
                "AND status NOT IN ('done', 'abandoned') "
                "ORDER BY sort_order, created_at",
                (kw, kw),
            )
        return [Task.from_row(r) for r in cur.fetchall()]

    def search_stickies(self, keyword: str) -> list[StickyNote]:
        """便签内容模糊匹配（与任务搜索共用同一关键词）。"""
        assert self.conn is not None
        cur = self.conn.execute(
            "SELECT * FROM sticky_note WHERE content LIKE ? ORDER BY sort_order, created_at",
            (f"%{keyword}%",),
        )
        return [StickyNote.from_row(r) for r in cur.fetchall()]

    # ------------------------------------------------------------ 热力图聚合（PRD F18）

    def daily_completion_counts(self, year: int) -> dict:
        """返回某年每日完成数 {date: count}（基于 task_completion，含周期任务历史完成）。"""
        assert self.conn is not None
        start = date(year, 1, 1)
        end = date(year + 1, 1, 1)
        cur = self.conn.execute(
            "SELECT completed_at FROM task_completion "
            "WHERE completed_at >= ? AND completed_at < ?",
            (start.isoformat(), end.isoformat()),
        )
        counts: dict = {}
        for row in cur.fetchall():
            d = datetime.fromisoformat(row["completed_at"]).date()
            counts[d] = counts.get(d, 0) + 1
        return counts

    def heatmap_summary(self, year: int, today: date | None = None) -> dict:
        """年度累计完成数 + 连续打卡天数（streak，PRD F18.6）。"""
        assert self.conn is not None
        today = today or date.today()
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM task_completion WHERE completed_at >= ? "
            "AND completed_at < ?",
            (date(year, 1, 1).isoformat(), date(year + 1, 1, 1).isoformat()),
        )
        total = int(cur.fetchone()[0] or 0)

        # 连续打卡：从今天往前数，直到出现「当日 0 完成」
        streak = 0
        if today.year == year:
            day = today
            counts = self.daily_completion_counts(year)
            while counts.get(day, 0) > 0:
                streak += 1
                day = day - timedelta(days=1)
        return {"total": total, "streak": streak}

    # ------------------------------------------------------------ 设置

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        assert self.conn is not None
        cur = self.conn.execute("SELECT value FROM setting WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value) -> None:
        self._write(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def get_int_setting(self, key: str, default: int) -> int:
        raw = self.get_setting(key)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------ 行为日志

    def log_event(self, event: str, **params) -> None:
        """写入本地行为日志（PRD 8.2，默认不上传）。"""
        assert self.conn is not None
        payload = json.dumps(params, ensure_ascii=False) if params else None
        self._write(
            "INSERT INTO behavior_log (event, params, created_at) VALUES (?, ?, ?)",
            (event, payload, datetime.now().isoformat()),
        )

    def prune_behavior_log(self) -> int:
        """清理过期日志：保留 30 天 / 最多 10000 条（PRD 4.5）。"""
        assert self.conn is not None
        cutoff = datetime.now() - timedelta(days=config.BEHAVIOR_LOG_RETENTION_DAYS)
        cur = self.conn.execute("DELETE FROM behavior_log WHERE created_at < ?", (cutoff.isoformat(),))
        deleted = cur.rowcount or 0
        self.conn.commit()

        cur = self.conn.execute("SELECT COUNT(*) FROM behavior_log")
        total = int(cur.fetchone()[0])
        if total > config.BEHAVIOR_LOG_MAX_ROWS:
            cur = self.conn.execute(
                "DELETE FROM behavior_log WHERE id IN ("
                "  SELECT id FROM behavior_log ORDER BY id ASC LIMIT ?"
                ")",
                (total - config.BEHAVIOR_LOG_MAX_ROWS,),
            )
            deleted += cur.rowcount or 0
            self.conn.commit()
        return deleted
