"""
任务反馈闭环测试：重新生成纪要 / 重新转写的状态、错误落库、WS 事件、启动看门狗
"""
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app_with_mocks(test_db, mock_all_globals):
    import main
    main._active_recording_configs.clear()
    main.ws_connections.clear()
    main.active_captures.clear()
    return main.app


@pytest.fixture
async def client(app_with_mocks):
    transport = ASGITransport(app=app_with_mocks)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_meeting(db, meeting_id="m1", status="done", error_message=None):
    await db.execute(
        "INSERT INTO meetings (id, title, status, error_message) VALUES (?, 'T', ?, ?)",
        (meeting_id, status, error_message),
    )
    await db.execute(
        "INSERT INTO transcript_segments (meeting_id, speaker, start_time, end_time, text) "
        "VALUES (?, 'Speaker A', 0, 5, 'hello')",
        (meeting_id,),
    )
    await db.commit()


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


# ============================================================
# regenerate 端点
# ============================================================


@pytest.mark.asyncio
async def test_regenerate_sets_processing_and_clears_error(test_db, mock_all_globals, client, monkeypatch):
    """regenerate：置 processing + 清空上次错误，不真正执行生成"""
    import main

    await _seed_meeting(test_db, status="error", error_message="old failure")
    # 拦掉真正的生成任务（create_task 异步执行，测试只验证端点同步副作用）
    monkeypatch.setattr(main, "generate_minutes", AsyncMock(return_value=None))

    res = await client.post("/api/meetings/m1/regenerate")
    assert res.status_code == 200

    cur = await test_db.execute("SELECT status, error_message FROM meetings WHERE id = 'm1'")
    row = await cur.fetchone()
    assert row[0] == "processing"
    assert row[1] is None


@pytest.mark.asyncio
async def test_regenerate_without_transcript_returns_404(test_db, mock_all_globals, client):
    await _seed_meeting(test_db)
    await test_db.execute("DELETE FROM transcript_segments WHERE meeting_id = 'm1'")
    await test_db.commit()

    res = await client.post("/api/meetings/m1/regenerate")
    assert res.status_code == 404


# ============================================================
# generate_minutes：错误落库 + WS 事件；成功写 language + 清错误
# ============================================================


@pytest.mark.asyncio
async def test_generate_minutes_failure_persists_error_and_pushes_ws(test_db, mock_all_globals):
    import main

    await _seed_meeting(test_db)
    main.summarizer.summarize = AsyncMock(side_effect=RuntimeError("api key invalid"))
    ws = FakeWS()
    main.ws_connections["m1"] = [ws]

    await main.generate_minutes("m1", "[Speaker A]: hello")

    cur = await test_db.execute("SELECT status, error_message FROM meetings WHERE id = 'm1'")
    row = await cur.fetchone()
    assert row[0] == "error"
    assert "api key invalid" in row[1]

    types = [m["type"] for m in ws.sent]
    assert "minutes_error" in types
    err = next(m for m in ws.sent if m["type"] == "minutes_error")
    assert "api key invalid" in err["message"]


@pytest.mark.asyncio
async def test_generate_minutes_success_writes_language_and_clears_error(test_db, mock_all_globals):
    import main

    await _seed_meeting(test_db, status="error", error_message="old failure")
    main.summarizer.summarize = AsyncMock(return_value={
        "summary": "Summary text", "key_points": ["k1"], "action_items": [], "next_steps": "", "raw_response": "",
    })
    main.summarizer.output_language = "en"
    main.summarizer.generate_title = AsyncMock(return_value=None)
    ws = FakeWS()
    main.ws_connections["m1"] = [ws]

    await main.generate_minutes("m1", "[Speaker A]: hello")

    cur = await test_db.execute(
        "SELECT m.status, m.error_message, mi.language FROM meetings m "
        "JOIN minutes mi ON mi.meeting_id = m.id WHERE m.id = 'm1'"
    )
    row = await cur.fetchone()
    assert row[0] == "done"
    assert row[1] is None
    assert row[2] == "en"
    assert "minutes_ready" in [m["type"] for m in ws.sent]


# ============================================================
# GET 响应带 error_message 与 minutes.language
# ============================================================


@pytest.mark.asyncio
async def test_get_meeting_returns_error_message(test_db, mock_all_globals, client):
    await _seed_meeting(test_db, status="error", error_message="boom")
    res = await client.get("/api/meetings/m1")
    assert res.status_code == 200
    data = res.json()
    assert data["meeting"]["error_message"] == "boom"


@pytest.mark.asyncio
async def test_get_meeting_returns_minutes_language(test_db, mock_all_globals, client):
    await _seed_meeting(test_db)
    await test_db.execute(
        "INSERT INTO minutes (meeting_id, summary, language) VALUES ('m1', 'Summary', 'en')"
    )
    await test_db.commit()
    res = await client.get("/api/meetings/m1")
    data = res.json()
    assert data["minutes"]["language"] == "en"


# ============================================================
# 启动看门狗：遗留 processing → error
# ============================================================


@pytest.mark.asyncio
async def test_watchdog_resets_stale_processing(test_db, mock_all_globals):
    import main

    await _seed_meeting(test_db, meeting_id="stale", status="processing")
    await _seed_meeting(test_db, meeting_id="done-m", status="done")

    await main._reset_stale_processing()

    cur = await test_db.execute("SELECT id, status, error_message FROM meetings ORDER BY id")
    rows = {r[0]: (r[1], r[2]) for r in await cur.fetchall()}
    assert rows["stale"] == ("error", "INTERRUPTED_BY_RESTART")
    assert rows["done-m"] == ("done", None)


# ============================================================
# 崩溃兜底：线程上下文 sqlite3 直连标记 error
# ============================================================


def test_mark_meeting_error_sync(temp_data_dir):
    """_mark_meeting_error_sync 用独立 sqlite3 连接写库（DATA_DIR 重定向生效）"""
    import sqlite3

    from storage.db import _db_path

    # 建表
    conn = sqlite3.connect(_db_path())
    conn.execute("""
        CREATE TABLE meetings (
            id TEXT PRIMARY KEY, title TEXT DEFAULT 'T', audio_path TEXT,
            duration_seconds INTEGER DEFAULT 0, created_at DATETIME,
            status TEXT DEFAULT 'processing', error_message TEXT
        )
    """)
    conn.execute("INSERT INTO meetings (id, status) VALUES ('crash-m', 'processing')")
    conn.commit()
    conn.close()

    import main
    main._mark_meeting_error_sync("crash-m", "loop crashed")

    conn = sqlite3.connect(_db_path())
    row = conn.execute("SELECT status, error_message FROM meetings WHERE id = 'crash-m'").fetchone()
    conn.close()
    assert row == ("error", "loop crashed")


# ============================================================
# 阶段事件
# ============================================================


@pytest.mark.asyncio
async def test_push_retranscribe_stage(test_db, mock_all_globals):
    import main

    ws = FakeWS()
    main.ws_connections["m1"] = [ws]
    await main._push_retranscribe_stage("m1", "transcribing")
    assert ws.sent == [{"type": "retranscribe_stage", "stage": "transcribing"}]
