"""应用路径与全局常量（PRD 4.4 关键实现约束 / 4.5 数据运维）。"""

from __future__ import annotations

import configparser
import os
import shutil
import sys
from pathlib import Path

APP_NAME = "QuadrantTodo"
APP_TITLE = "四象限待办"
ORG_NAME = "QuadrantTodo"

VERSION = "1.2.2"


def _anchor_dir() -> Path:
    """锚点目录：exe（或启动脚本）所在目录，用于放置配置与默认数据（便携存储）。"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    elif sys.argv:
        base = Path(sys.argv[0]).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    return base


ANCHOR_DIR = _anchor_dir()


def _icon_path() -> Path:
    """应用图标路径。

    PyInstaller --onedir 会把 --add-data 的资源放在 _internal 下（运行时为
    sys._MEIPASS），而不是 exe 同级目录；开发态则位于项目根 assets/。
    两个位置都尝试，保证打包前后都能加载到图标。
    """
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / "app.ico")
    candidates.append(ANCHOR_DIR / "assets" / "app.ico")
    candidates.append(Path(__file__).resolve().parent.parent / "assets" / "app.ico")
    for path in candidates:
        if path.exists():
            return path
    return candidates[0] if candidates else ANCHOR_DIR / "assets" / "app.ico"


#: 应用图标（打包后在 _internal/assets，开发态在项目根 assets）
APP_ICON_PATH = _icon_path()
#: 持久化数据目录位置的 ini（位于锚点目录，不随数据移动而丢失）
_CONFIG_INI = ANCHOR_DIR / "app_config.ini"
_INI_SECTION = "storage"
_INI_KEY_DATA_DIR = "data_dir"


def _read_ini_data_dir() -> Path | None:
    """读取 ini 中记录的数据目录（用户自定义位置）。"""
    if not _CONFIG_INI.exists():
        return None
    cp = configparser.ConfigParser()
    try:
        cp.read(_CONFIG_INI, encoding="utf-8")
        val = cp.get(_INI_SECTION, _INI_KEY_DATA_DIR, fallback="").strip()
        if val and Path(val).is_absolute():
            return Path(val)
    except (configparser.Error, OSError):
        pass
    return None


def save_data_dir(new_dir) -> None:
    """持久化数据目录位置到 ini（PRD：设置中可修改数据文件位置）。"""
    cp = configparser.ConfigParser()
    if _CONFIG_INI.exists():
        try:
            cp.read(_CONFIG_INI, encoding="utf-8")
        except configparser.Error:
            pass
    if not cp.has_section(_INI_SECTION):
        cp.add_section(_INI_SECTION)
    cp.set(_INI_SECTION, _INI_KEY_DATA_DIR, str(Path(new_dir).resolve()))
    with open(_CONFIG_INI, "w", encoding="utf-8") as fh:
        cp.write(fh)


def migrate_data_to(new_dir) -> list:
    """把当前数据目录的 data.db / -wal / -shm / backups / logs 移动到 new_dir。

    返回已移动的项名称；目标已存在同名项则跳过，避免覆盖。
    """
    new_dir = Path(new_dir).resolve()
    new_dir.mkdir(parents=True, exist_ok=True)
    moved: list = []
    for name in ("data.db", "data.db-wal", "data.db-shm"):
        src = DATA_DIR / name
        if src.exists() and not (new_dir / name).exists():
            shutil.move(str(src), str(new_dir / name))
            moved.append(name)
    for sub in ("backups", "logs"):
        src = DATA_DIR / sub
        if src.exists() and not (new_dir / sub).exists():
            shutil.move(str(src), str(new_dir / sub))
            moved.append(sub)
    return moved


def _resolve_data_dir() -> Path:
    """数据目录解析优先级：环境变量 QUADRANT_DATA_DIR > ini 自定义 > exe 同目录（便携默认）。"""
    env = os.environ.get("QUADRANT_DATA_DIR")
    if env and Path(env).is_absolute():
        return Path(env)
    ini = _read_ini_data_dir()
    if ini is not None:
        return ini
    return ANCHOR_DIR


DATA_DIR = _resolve_data_dir()
DB_PATH = DATA_DIR / "data.db"
BACKUP_DIR = DATA_DIR / "backups"
LOG_DIR = DATA_DIR / "logs"


def ensure_dirs() -> None:
    """确保数据、备份、日志目录存在。"""
    for path in (DATA_DIR, BACKUP_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- setting 键

KEY_THRESHOLD = "urgency_threshold"      # 历史键：旧版单一紧急阈值，作为「重要任务阈值」回退
KEY_THRESHOLD_IMPORTANT = "urgency_threshold_important"        # 重要任务紧急窗口（天），控制 Q1↔Q2
KEY_THRESHOLD_UNIMPORTANT = "urgency_threshold_unimportant"    # 不重要任务紧急窗口（天），控制 Q3↔Q4
KEY_GEOMETRY = "window_geometry"         # 窗口几何，PRD F11.1
KEY_WINDOW_STATE = "window_state"         # 窗口状态：maximized / normal，双击打开默认全屏
KEY_LAST_OPEN_DATE = "last_open_date"    # 跨日检测基准，PRD F12.1
KEY_AUTOSTART = "auto_start"             # 开机启动，PRD F8.7
KEY_ONBOARDING_DONE = "onboarding_done"  # 首次引导是否已关闭，PRD F1.13

# ---------------------------------------------------------------- 二期新增设置键

KEY_THEME = "theme"                          # 主题：system / light / dark，PRD F24
KEY_DEFAULT_REMINDER_RULE = "default_reminder_rule"   # 默认提醒联动：none / relative，PRD F26.3
KEY_DEFAULT_REMINDER_OFFSET = "default_reminder_offset"  # 默认相对提前分钟，PRD F26.3
KEY_POMODORO_DURATION = "pomodoro_duration"  # 番茄钟默认时长(分)，PRD F23.1 / 2.4

#: 自动备份策略（PRD F10.5 扩展：频率 / 时间点 / 保留份数可在设置中调整）
KEY_BACKUP_ENABLED = "backup_enabled"            # 是否启用自动备份：1 / 0
KEY_BACKUP_INTERVAL_DAYS = "backup_interval_days"  # 多少天备份一次
KEY_BACKUP_TIME = "backup_time"                  # 每天执行备份的时刻 HH:MM:SS
KEY_BACKUP_KEEP_COUNT = "backup_keep_count"      # 保留份数
KEY_LAST_BACKUP_AT = "last_backup_at"            # 上次备份时间（ISO 8601），判断是否需要备份

#: 备份默认值
DEFAULT_BACKUP_ENABLED = "1"
DEFAULT_BACKUP_INTERVAL_DAYS = 1
DEFAULT_BACKUP_TIME = "20:00:00"
#: 保留份数默认值的唯一真源，运维参数的 BACKUP_KEEP_COUNT 反向引用它
DEFAULT_BACKUP_KEEP_COUNT = 7

#: 备份参数可调范围
BACKUP_MIN_INTERVAL_DAYS = 1
BACKUP_MAX_INTERVAL_DAYS = 30
BACKUP_MIN_KEEP_COUNT = 1
BACKUP_MAX_KEEP_COUNT = 100

#: 系统级全局热键（PRD F25），默认关闭，安全优先
KEY_GLOBAL_HOTKEY_ENABLED = "global_hotkey_enabled"
KEY_HOTKEY_TOGGLE = "hotkey_toggle"          # 呼出/隐藏主窗口
KEY_HOTKEY_QUICKADD = "hotkey_quickadd"      # 全局快速添加

#: 全局热键默认值（PRD F25.2）
DEFAULT_HOTKEY_TOGGLE = "Ctrl+Alt+Q"
DEFAULT_HOTKEY_QUICKADD = "Ctrl+Alt+N"

#: 番茄钟可配时长范围（分钟，PRD F23.1 / 2.4）
POMODORO_MIN_MINUTES = 5
POMODORO_MAX_MINUTES = 60

#: 主题取值（PRD F24）
THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_VALUES = (THEME_SYSTEM, THEME_LIGHT, THEME_DARK)

#: 周期类型（PRD F16.1 / 2.2）
CYCLE_NONE = "none"
CYCLE_DAILY = "daily"
CYCLE_WEEKLY = "weekly"
CYCLE_MONTHLY = "monthly"
CYCLE_CUSTOM = "custom"
CYCLE_VALUES = (CYCLE_NONE, CYCLE_DAILY, CYCLE_WEEKLY, CYCLE_MONTHLY, CYCLE_CUSTOM)

#: 提醒联动规则（PRD F26.1）
REMINDER_RULE_NONE = "none"
REMINDER_RULE_MANUAL = "manual"
REMINDER_RULE_RELATIVE = "relative"
REMINDER_RULE_VALUES = (REMINDER_RULE_NONE, REMINDER_RULE_MANUAL, REMINDER_RULE_RELATIVE)

#: 默认相对提醒提前分钟（PRD 2.4）
DEFAULT_REMINDER_OFFSET = 60

# ---------------------------------------------------------------- 运维参数（PRD 4.5）

BEHAVIOR_LOG_RETENTION_DAYS = 30
BEHAVIOR_LOG_MAX_ROWS = 10000
BACKUP_KEEP_COUNT = DEFAULT_BACKUP_KEEP_COUNT
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# ---------------------------------------------------------------- 界面参数

#: 超过该数量启用控件复用，PRD 4.6
VIRTUAL_SCROLL_THRESHOLD = 200

#: 撤销提示条显示时长（秒），PRD F5.7
UNDO_TIMEOUT_SECONDS = 5

#: 提醒轮询与跨日检测间隔（毫秒），PRD F9.1 / F12.1
TICK_INTERVAL_MS = 60 * 1000

#: 单实例标识，PRD F7.1
LOCAL_SERVER_NAME = "QuadrantTodoSingleInstance"

#: 预计耗时下拉选项（分钟），PRD F5.10
ESTIMATE_PRESETS = (15, 30, 60, 120, 240)
