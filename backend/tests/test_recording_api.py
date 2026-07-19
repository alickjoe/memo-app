"""
B. 录制 API 集成测试 (6 cases)
测试 /api/record/start 和 /api/record/switch-device 端点
"""
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def app_with_mocks(test_db, mock_all_globals):
    """创建已注入 mock 的 FastAPI app"""
    import main
    main._active_recording_configs.clear()
    main.ws_connections.clear()
    main.active_captures.clear()
    return main.app


@pytest.fixture
async def client(app_with_mocks):
    """异步 HTTP 测试客户端"""
    transport = ASGITransport(app=app_with_mocks)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ============================================================
# B1: 无配置时使用默认值
# ============================================================
@pytest.mark.asyncio
async def test_start_recording_with_default_config(
    test_db, mock_all_globals, client,
):
    """无 body 时使用 RecordingConfig 默认值"""
    res = await client.post("/api/record/start")
    assert res.status_code == 200
    data = res.json()
    assert "meeting_id" in data
    assert data["status"] == "recording"
    assert "config" in data
    assert data["config"]["segmentation_strategy"] == "hybrid"
    assert data["config"]["max_segment_duration"] == 15


# ============================================================
# B2: 自定义配置覆盖默认值
# ============================================================
@pytest.mark.asyncio
async def test_start_recording_with_custom_config(
    test_db, mock_all_globals, client,
):
    """请求中传入的 config 应覆盖默认值"""
    import main

    res = await client.post("/api/record/start", json={
        "config": {
            "segmentation_strategy": "fixed",
            "fixed_chunk_duration": 20,
        }
    })
    assert res.status_code == 200
    data = res.json()
    assert data["config"]["segmentation_strategy"] == "fixed"
    assert data["config"]["fixed_chunk_duration"] == 20

    # 验证全局配置也被设置
    meeting_id = data["meeting_id"]
    stored = main._active_recording_configs.get(meeting_id)
    assert stored is not None
    assert stored.segmentation_strategy == "fixed"
    assert stored.fixed_chunk_duration == 20


# ============================================================
# B3: start 返回设备名且不传 device_id
# ============================================================
@pytest.mark.asyncio
async def test_start_recording_returns_device_names(
    test_db, mock_all_globals, client,
):
    """响应中包含 loopback_device_name 和 input_device_name，start 不传 device_id"""
    mocks = mock_all_globals
    capture = mocks["audio_capture"]

    res = await client.post("/api/record/start", json={
        "config": {"segmentation_strategy": "vad"},
    })
    assert res.status_code == 200
    data = res.json()
    assert "loopback_device_name" in data
    assert "input_device_name" in data

    # 验证 capture.start 被调用时没有传 device_id
    capture.start.assert_called_once()
    call_kwargs = capture.start.call_args.kwargs
    assert "loopback_device_id" not in call_kwargs
    assert "input_device_id" not in call_kwargs


# ============================================================
# B4: 请求配置优先于 settings 默认值
# ============================================================
@pytest.mark.asyncio
async def test_start_recording_config_takes_priority_over_settings(
    test_db, mock_all_globals, client,
):
    """请求级 config 覆盖 settings 表中的默认值"""
    # 先保存 settings 默认值
    await client.put("/api/settings", json={
        "recording_segmentation_strategy": "fixed",
        "recording_max_segment_duration": "30",
    })

    # 请求覆盖为 vad
    res = await client.post("/api/record/start", json={
        "config": {
            "segmentation_strategy": "vad",
        }
    })
    assert res.status_code == 200
    data = res.json()
    # 请求的值应生效
    assert data["config"]["segmentation_strategy"] == "vad"
    # 未指定的应使用 settings 默认值
    assert data["config"]["max_segment_duration"] == 30


# ============================================================
# B5: start 异常时清理配置
# ============================================================
@pytest.mark.asyncio
async def test_config_cleanup_on_error(
    test_db, mock_all_globals, client,
):
    """audio_capture.start 抛异常时不应残留 _active_recording_configs"""
    import main

    mocks = mock_all_globals
    capture = mocks["audio_capture"]
    capture.start.side_effect = Exception("Device not available")

    res = await client.post("/api/record/start", json={
        "config": {"segmentation_strategy": "fixed"}
    })
    assert res.status_code == 500
    data = res.json()
    assert "error" in data

    # 确认没有残留配置
    assert len(main._active_recording_configs) == 0


# ============================================================
# B6: 手动切换设备端点
# ============================================================
@pytest.mark.asyncio
async def test_switch_device_endpoint(
    test_db, mock_all_globals, client,
):
    """POST /api/record/switch-device 应调用 capture.switch_device"""
    mocks = mock_all_globals
    capture = mocks["audio_capture"]
    capture.switch_device = lambda device_type, device_id: True

    res = await client.post("/api/record/switch-device", json={
        "device_type": "loopback",
        "device_id": "test-dev-001",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "switched"
    assert data["device_type"] == "loopback"
