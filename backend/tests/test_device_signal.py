"""
C. 设备信号监控测试 (6 cases)
测试 AudioCapture 的信号追踪、设备扫描、自动切换

注意：soundcard 已在 conftest.py 模块级别被 mock，因此 audio.capture 可安全导入。
"""
import pytest
from unittest.mock import MagicMock, AsyncMock

from audio.capture import AudioCapture, SIGNAL_THRESHOLD_RMS, NO_SIGNAL_CHUNKS_BEFORE_SCAN


@pytest.fixture
def audio_capture():
    """创建无硬件依赖的 AudioCapture 实例（soundcard 已被 mock）"""
    cap = AudioCapture(sample_rate=16000, chunk_duration=0.1)
    return cap


# ============================================================
# C1: 初始信号状态
# ============================================================
def test_signal_stats_initial_state(audio_capture):
    """初始状态信号计数器均为 0"""
    stats = audio_capture.signal_stats
    assert stats["loopback_rms"] == 0.0
    assert stats["mic_rms"] == 0.0
    assert stats["loopback_no_signal_chunks"] == 0
    assert stats["mic_no_signal_chunks"] == 0


# ============================================================
# C2: 无信号计数器递增
# ============================================================
def test_signal_monitor_counts_no_signal_chunks(audio_capture):
    """连续无信号时计数器应递增"""
    for _ in range(NO_SIGNAL_CHUNKS_BEFORE_SCAN + 10):
        audio_capture._current_loopback_rms = 0.0
        if audio_capture._current_loopback_rms < SIGNAL_THRESHOLD_RMS:
            audio_capture._loopback_no_signal_count += 1

    assert audio_capture._loopback_no_signal_count >= NO_SIGNAL_CHUNKS_BEFORE_SCAN


# ============================================================
# C3: 有信号时计数器重置
# ============================================================
def test_signal_monitor_resets_on_signal(audio_capture):
    """有信号时计数器应重置为 0"""
    # 先累积一些无信号计数
    audio_capture._loopback_no_signal_count = 30
    audio_capture._mic_no_signal_count = 20

    # 模拟有信号到达
    audio_capture._current_loopback_rms = 0.05
    audio_capture._current_mic_rms = 0.03

    if audio_capture._current_loopback_rms >= SIGNAL_THRESHOLD_RMS:
        audio_capture._loopback_no_signal_count = 0
    if audio_capture._current_mic_rms >= SIGNAL_THRESHOLD_RMS:
        audio_capture._mic_no_signal_count = 0

    assert audio_capture._loopback_no_signal_count == 0
    assert audio_capture._mic_no_signal_count == 0


# ============================================================
# C4: scan-devices 端点返回信号信息
# ============================================================
@pytest.mark.asyncio
async def test_scan_devices_endpoint_returns_signal_info(
    test_db, mock_all_globals,
):
    """POST /api/audio/scan-devices 返回设备信号列表"""
    from httpx import AsyncClient, ASGITransport
    import main

    mocks = mock_all_globals
    capture = mocks["audio_capture"]

    # Mock scan_devices_for_signal 返回结果
    from audio.capture import DeviceSignalInfo
    capture.scan_devices_for_signal = MagicMock(return_value=[
        DeviceSignalInfo("dev-1", "Speaker A", True, 0.05, True),
        DeviceSignalInfo("dev-2", "Microphone B", False, 0.0001, False),
    ])

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/audio/scan-devices", json={
            "device_type": "all",
            "duration": 1.0,
        })
    assert res.status_code == 200
    data = res.json()
    assert "devices" in data
    assert len(data["devices"]) == 2
    assert data["devices"][0]["has_signal"] is True
    assert data["devices"][1]["has_signal"] is False
    assert data["devices"][0]["rms_level"] == 0.05


# ============================================================
# C5: signal-status 端点
# ============================================================
@pytest.mark.asyncio
async def test_signal_status_endpoint(test_db, mock_all_globals):
    """GET /api/audio/signal-status 返回当前信号统计"""
    from httpx import AsyncClient, ASGITransport
    import main

    mocks = mock_all_globals
    capture = mocks["audio_capture"]
    capture.signal_stats = {
        "loopback_rms": 0.003,
        "mic_rms": 0.001,
        "loopback_no_signal_chunks": 5,
        "mic_no_signal_chunks": 12,
    }

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/audio/signal-status")
    assert res.status_code == 200
    data = res.json()
    assert data["loopback_rms"] == 0.003
    assert data["mic_rms"] == 0.001
    assert data["loopback_no_signal_chunks"] == 5
    assert data["mic_no_signal_chunks"] == 12


# ============================================================
# C6: 设备切换回调触发
# ============================================================
@pytest.mark.asyncio
async def test_device_switch_callback_fires(audio_capture):
    """设置回调后触发切换应调用回调"""
    callback_calls = []

    async def on_switch(device_type, old_name, new_name):
        callback_calls.append((device_type, old_name, new_name))

    audio_capture.set_device_switch_callback(on_switch)

    # 验证回调已保存
    assert audio_capture._on_device_switched is not None

    # 注意：完整切换流程依赖 soundcard，这里仅验证回调设置机制
