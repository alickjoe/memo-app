"""
D. 录音默认配置 CRUD 测试 (4 cases)
测试 /api/settings 对 recording_* 键的读写
"""
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def app_for_settings(test_db, mock_all_globals):
    import main
    main._active_recording_configs.clear()
    return main.app


@pytest.fixture
async def client(app_for_settings):
    transport = ASGITransport(app=app_for_settings)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ============================================================
# D1: 保存录音默认配置
# ============================================================
@pytest.mark.asyncio
async def test_settings_save_recording_defaults(test_db, mock_all_globals, client):
    """PUT /api/settings 保存 recording_* 键"""
    res = await client.put("/api/settings", json={
        "recording_segmentation_strategy": "fixed",
        "recording_max_segment_duration": "20",
    })
    assert res.status_code == 200
    assert res.json()["status"] == "saved"


# ============================================================
# D2: 读取录音默认配置
# ============================================================
@pytest.mark.asyncio
async def test_settings_read_recording_defaults(test_db, mock_all_globals, client):
    """GET /api/settings 返回已保存的 recording_* 值"""
    # 先保存
    await client.put("/api/settings", json={
        "recording_segmentation_strategy": "fixed",
        "recording_max_segment_duration": "20",
    })

    # 再读取
    res = await client.get("/api/settings")
    assert res.status_code == 200
    data = res.json()
    assert data["recording_segmentation_strategy"] == "fixed"
    assert data["recording_max_segment_duration"] == "20"


# ============================================================
# D3: 默认值被 start 端使用
# ============================================================
@pytest.mark.asyncio
async def test_load_recording_defaults_uses_saved_values(test_db, mock_all_globals, client):
    """保存 settings 后，无 body 的 start 请求应使用保存的默认值"""
    await client.put("/api/settings", json={
        "recording_segmentation_strategy": "fixed",
        "recording_fixed_chunk_duration": "45",
    })

    res = await client.post("/api/record/start")
    assert res.status_code == 200
    data = res.json()
    assert data["config"]["segmentation_strategy"] == "fixed"
    assert data["config"]["fixed_chunk_duration"] == 45


# ============================================================
# D4: 部分更新不影响其他键
# ============================================================
@pytest.mark.asyncio
async def test_settings_partial_update_preserves_other_keys(test_db, mock_all_globals, client):
    """PUT 只更新部分键时其他键保持不变"""
    # 保存全部
    await client.put("/api/settings", json={
        "recording_segmentation_strategy": "hybrid",
        "recording_max_segment_duration": "15",
        "recording_vad_threshold": "0.6",
    })

    # 仅更新 one key
    await client.put("/api/settings", json={
        "recording_vad_threshold": "0.8",
    })

    # 读取验证
    res = await client.get("/api/settings")
    data = res.json()
    assert data["recording_segmentation_strategy"] == "hybrid"  # 不变
    assert data["recording_max_segment_duration"] == "15"       # 不变
    assert data["recording_vad_threshold"] == "0.8"             # 更新
