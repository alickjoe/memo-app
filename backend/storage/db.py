"""
SQLite 数据库管理模块
"""
import os
import aiosqlite
import logging

logger = logging.getLogger("memo.db")

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    """获取数据库连接（单例）"""
    global _db
    if _db is None:
        data_dir = os.environ.get("DATA_DIR", os.path.join(os.path.expanduser("~"), ".memo"))
        os.makedirs(data_dir, exist_ok=True)
        db_path = os.path.join(data_dir, "memo.db")
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
            title TEXT DEFAULT '未命名会议',
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
