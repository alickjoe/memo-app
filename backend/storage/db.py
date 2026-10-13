"""
SQLite 数据库管理模块
"""
import logging
import os

import aiosqlite

logger = logging.getLogger("memo.db")

_db: aiosqlite.Connection | None = None


def _db_path() -> str:
    """数据库文件路径（线程上下文直连 sqlite3 时也用此路径）"""
    data_dir = os.environ.get("DATA_DIR", os.path.join(os.path.expanduser("~"), ".memo"))
    return os.path.join(data_dir, "memo.db")


async def get_db() -> aiosqlite.Connection:
    """获取数据库连接（单例）"""
    global _db
    if _db is None:
        data_dir = os.path.dirname(_db_path())
        os.makedirs(data_dir, exist_ok=True)
        db_path = _db_path()
        _db = await aiosqlite.connect(db_path)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        logger.info(f"Database connected: {db_path}")
    return _db


async def init_db():
    """初始化数据库表"""
    db = await get_db()

    await db.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id TEXT PRIMARY KEY,
            title TEXT DEFAULT 'Untitled Meeting',
            audio_path TEXT,
            duration_seconds INTEGER DEFAULT 0,
            created_at DATETIME,
            status TEXT DEFAULT 'processing'
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS transcript_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id TEXT,
            speaker TEXT DEFAULT 'Speaker A',
            start_time REAL,
            end_time REAL,
            text TEXT,
            version INTEGER DEFAULT 1,
            FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS minutes (
            meeting_id TEXT PRIMARY KEY,
            summary TEXT,
            key_points TEXT,
            action_items TEXT,
            next_steps TEXT,
            raw_response TEXT,
            generated_at DATETIME,
            FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    await db.commit()

    # 迁移：为已有 transcript_segments 添加 version 列
    await _migrate_add_version_column(db)
    # 迁移：为已有 meetings 添加 error_message 列（任务失败原因）
    await _migrate_add_column(db, "meetings", "error_message", "TEXT")
    # 迁移：为已有 minutes 添加 language 列（纪要输出语言，前端标题渲染用）
    await _migrate_add_column(db, "minutes", "language", "TEXT")

    logger.info("Database tables initialized")


async def close_db():
    """关闭数据库连接"""
    global _db
    if _db:
        await _db.close()
        _db = None
        logger.info("Database connection closed")


async def _migrate_add_version_column(db: aiosqlite.Connection):
    """为已有数据库添加 version 列（幂等迁移）"""
    try:
        cursor = await db.execute("PRAGMA table_info(transcript_segments)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "version" not in columns:
            await db.execute(
                "ALTER TABLE transcript_segments ADD COLUMN version INTEGER DEFAULT 1"
            )
            await db.commit()
            logger.info("Migration: added version column to transcript_segments")
    except Exception as e:
        logger.warning(f"Migration check skipped: {e}")


async def _migrate_add_column(db: aiosqlite.Connection, table: str, column: str, col_type: str):
    """为已有表添加列（幂等迁移）"""
    try:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in await cursor.fetchall()]
        if column not in columns:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            await db.commit()
            logger.info(f"Migration: added {column} column to {table}")
    except Exception as e:
        logger.warning(f"Migration check skipped ({table}.{column}): {e}")
