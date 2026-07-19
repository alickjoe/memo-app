"""
A. 分段策略单元测试 (6 cases)
测试 process_audio_pipeline 在不同策略下的分段行为
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from tests.helpers import make_audio_bytes, make_empty_chunk


# ============================================================
# A1: 连续语音触发 max_duration 强制分段
# ============================================================
@pytest.mark.asyncio
async def test_max_duration_triggers_on_continuous_speech(
    test_db, mock_all_globals,
):
    """Bug 修复验证：连续语音下 max_segment_duration 应在 15s 触发分段"""
    import main
    from main import RecordingConfig

    mocks = mock_all_globals
    capture = mocks["audio_capture"]
    vad = mocks["vad"]
    stt = mocks["stt_engine"]

    # 配置: hybrid 策略, max_segment_duration=5s (加速测试)
    config = RecordingConfig(
        segmentation_strategy="hybrid",
        max_segment_duration=5.0,
        vad_silence_frames=8,
    )
    main._active_recording_configs["test-meeting-a1"] = config

    # Mock: 始终返回 speech（模拟连续播放音频）
    vad.detect_with_hysteresis = AsyncMock(return_value='speech')
    # Mock: STT 返回有效文本
    stt.transcribe_with_validation = AsyncMock(return_value="测试转写内容")

    # 生成 10s 的音频数据 (16000Hz * 2 bytes * 10 = 320000 bytes)
    # 分成 0.1s chunks = 100 个 chunks
    total_duration = 10.0
    chunks = []
    for _ in range(int(total_duration / 0.1)):
        chunks.append(make_audio_bytes(0.1, with_signal=True))

    call_count = [0]
    stop = [False]

    async def read_chunk_side_effect(timeout=1.0):
        if call_count[0] < len(chunks):
            chunk = chunks[call_count[0]]
            call_count[0] += 1
            return chunk
        stop[0] = True
        return None

    capture.read_chunk = AsyncMock(side_effect=read_chunk_side_effect)
    capture.is_active = MagicMock()
    capture.is_active.side_effect = lambda: not stop[0]

    # 执行 pipeline
    await main.process_audio_pipeline("test-meeting-a1")

    # 验证: 10s / 5s = 至少 1 个 segment，至多 2 个（取决于 chunk 边界）
    # STT 至少被调用
    assert stt.transcribe_with_validation.called, \
        "STT should have been called for segments"
    call_count_stt = stt.transcribe_with_validation.call_count
    assert call_count_stt >= 1, \
        f"Expected at least 1 segment, got {call_count_stt}"


# ============================================================
# A2: 长停顿触发断句
# ============================================================
@pytest.mark.asyncio
async def test_long_pause_triggers_segmentation(
    test_db, mock_all_globals,
):
    """验证 silence 帧积累到阈值后触发分段"""
    import main
    from main import RecordingConfig

    mocks = mock_all_globals
    capture = mocks["audio_capture"]
    vad = mocks["vad"]
    stt = mocks["stt_engine"]

    config = RecordingConfig(
        segmentation_strategy="vad",
        max_segment_duration=60.0,  # 不给 max_duration 触发机会
        vad_silence_frames=5,       # 5 帧 = 0.5s
    )
    main._active_recording_configs["test-meeting-a2"] = config

    stt.transcribe_with_validation = AsyncMock(return_value="停顿后的转写")

    # 状态机: speech 20帧(2s) → silence 10帧(1s) → speech 10帧(1s) → silence...
    states = (
        ['speech'] * 20 + ['silence'] * 10 + ['speech'] * 10
    )
    state_idx = [0]

    async def vad_side_effect(chunk):
        s = states[min(state_idx[0], len(states) - 1)]
        state_idx[0] += 1
        return s

    vad.detect_with_hysteresis = AsyncMock(side_effect=vad_side_effect)

    chunks = [make_audio_bytes(0.1) for _ in states]
    call_idx = [0]

    async def read_chunk_side_effect(timeout=1.0):
        if call_idx[0] < len(chunks):
            c = chunks[call_idx[0]]
            call_idx[0] += 1
            return c
        call_idx[0] += 1
        return None

    capture.read_chunk = AsyncMock(side_effect=read_chunk_side_effect)
    capture.is_active = MagicMock()
    capture.is_active.side_effect = lambda: call_idx[0] <= len(chunks)

    await main.process_audio_pipeline("test-meeting-a2")

    # 验证: silence 后触发了至少 1 个 segment
    assert stt.transcribe_with_validation.called, \
        "Expected segmentation after long pause"


# ============================================================
# A3: fixed 策略按时间切分
# ============================================================
@pytest.mark.asyncio
async def test_fixed_strategy_chunks_by_duration(
    test_db, mock_all_globals,
):
    """fixed 策略应忽略 VAD，按 fixed_chunk_duration 切分"""
    import main
    from main import RecordingConfig

    mocks = mock_all_globals
    capture = mocks["audio_capture"]
    vad = mocks["vad"]
    stt = mocks["stt_engine"]

    config = RecordingConfig(
        segmentation_strategy="fixed",
        fixed_chunk_duration=2.0,  # 2s 一个 chunk (加速测试)
    )
    main._active_recording_configs["test-meeting-a3"] = config

    stt.transcribe_with_validation = AsyncMock(return_value="fixed chunk text")

    # 生成 6s 音频 = 60 chunks x 0.1s
    total_duration = 6.0
    chunks = [make_audio_bytes(0.1) for _ in range(int(total_duration / 0.1))]
    call_idx = [0]

    async def read_chunk_side_effect(timeout=1.0):
        if call_idx[0] < len(chunks):
            c = chunks[call_idx[0]]
            call_idx[0] += 1
            return c
        call_idx[0] += 1
        return None

    capture.read_chunk = AsyncMock(side_effect=read_chunk_side_effect)
    capture.is_active = MagicMock()
    capture.is_active.side_effect = lambda: call_idx[0] <= len(chunks)

    await main.process_audio_pipeline("test-meeting-a3")

    # 6s / 2s = 3 segments
    count = stt.transcribe_with_validation.call_count
    assert count == 3, \
        f"Fixed strategy: expected 3 segments (6s/2s), got {count}"


# ============================================================
# A4: VAD 降级回退到 fixed 策略
# ============================================================
@pytest.mark.asyncio
async def test_vad_degraded_falls_back_to_fixed(
    test_db, mock_all_globals,
):
    """VAD 加载失败时自动切换为 fixed 策略"""
    import main
    from main import RecordingConfig

    mocks = mock_all_globals
    capture = mocks["audio_capture"]
    vad = mocks["vad"]
    stt = mocks["stt_engine"]

    # 模拟 VAD 降级
    vad.is_degraded = True

    config = RecordingConfig(
        segmentation_strategy="vad",
        fixed_chunk_duration=2.0,
    )
    main._active_recording_configs["test-meeting-a4"] = config

    stt.transcribe_with_validation = AsyncMock(return_value="fallback fixed text")

    chunks = [make_audio_bytes(0.1) for _ in range(40)]  # 4s
    call_idx = [0]

    async def read_chunk_side_effect(timeout=1.0):
        if call_idx[0] < len(chunks):
            c = chunks[call_idx[0]]
            call_idx[0] += 1
            return c
        call_idx[0] += 1
        return None

    capture.read_chunk = AsyncMock(side_effect=read_chunk_side_effect)
    capture.is_active = MagicMock()
    capture.is_active.side_effect = lambda: call_idx[0] <= len(chunks)

    await main.process_audio_pipeline("test-meeting-a4")

    # 降级后应使用 fixed 策略切分
    assert stt.transcribe_with_validation.called, \
        "Should have segments via fixed fallback"
    # 4s / 2s = 2 segments
    assert stt.transcribe_with_validation.call_count == 2, \
        f"Expected 2 fixed-chunk segments, got {stt.transcribe_with_validation.call_count}"


# ============================================================
# A5: 太短的 segment 被跳过
# ============================================================
@pytest.mark.asyncio
async def test_segment_too_short_skipped(
    test_db, mock_all_globals,
):
    """不足 min_speech_bytes 的语音段应被跳过"""
    import main
    from main import RecordingConfig

    mocks = mock_all_globals
    capture = mocks["audio_capture"]
    vad = mocks["vad"]
    stt = mocks["stt_engine"]

    config = RecordingConfig(
        segmentation_strategy="hybrid",
        max_segment_duration=60.0,
        vad_silence_frames=3,
        min_speech_bytes=32000,  # 2s
    )
    main._active_recording_configs["test-meeting-a5"] = config

    stt.transcribe_with_validation = AsyncMock(return_value="should not be called")

    # speech 5 帧 (0.5s) → silence 5 帧 → speech 5 帧
    states = ['speech'] * 5 + ['silence'] * 5 + ['speech'] * 5
    state_idx = [0]

    async def vad_side_effect(chunk):
        s = states[min(state_idx[0], len(states) - 1)]
        state_idx[0] += 1
        return s

    vad.detect_with_hysteresis = AsyncMock(side_effect=vad_side_effect)

    chunks = [make_audio_bytes(0.1) for _ in states]
    call_idx = [0]

    async def read_chunk_side_effect(timeout=1.0):
        if call_idx[0] < len(chunks):
            c = chunks[call_idx[0]]
            call_idx[0] += 1
            return c
        call_idx[0] += 1
        return None

    capture.read_chunk = AsyncMock(side_effect=read_chunk_side_effect)
    capture.is_active = MagicMock()
    capture.is_active.side_effect = lambda: call_idx[0] <= len(chunks)

    await main.process_audio_pipeline("test-meeting-a5")

    # 0.5s 语音 < 2s 最小阈值, 不应触发 STT
    assert stt.transcribe_with_validation.call_count == 0, \
        f"Short segments should be skipped, got {stt.transcribe_with_validation.call_count}"


# ============================================================
# A6: 停止时处理剩余 buffer
# ============================================================
@pytest.mark.asyncio
async def test_remaining_buffer_processed_on_stop(
    test_db, mock_all_globals,
):
    """录制停止时 finally 块应处理剩余缓冲区"""
    import main
    from main import RecordingConfig

    mocks = mock_all_globals
    capture = mocks["audio_capture"]
    vad = mocks["vad"]
    stt = mocks["stt_engine"]

    config = RecordingConfig(
        segmentation_strategy="hybrid",
        max_segment_duration=60.0,
        vad_silence_frames=50,  # 设大避免中间触发
    )
    main._active_recording_configs["test-meeting-a6"] = config

    stt.transcribe_with_validation = AsyncMock(return_value="final segment text")

    # 一直 speech，中间不触发分段
    vad.detect_with_hysteresis = AsyncMock(return_value='speech')

    total_duration = 3.0  # 3s > 2s min_speech
    chunks = [make_audio_bytes(0.1) for _ in range(int(total_duration / 0.1))]
    call_idx = [0]

    async def read_chunk_side_effect(timeout=1.0):
        if call_idx[0] < len(chunks):
            c = chunks[call_idx[0]]
            call_idx[0] += 1
            return c
        call_idx[0] += 1
        return None

    capture.read_chunk = AsyncMock(side_effect=read_chunk_side_effect)
    capture.is_active = MagicMock()
    capture.is_active.side_effect = lambda: call_idx[0] <= len(chunks)

    await main.process_audio_pipeline("test-meeting-a6")

    # finally 块应触发至少 1 次 STT（剩余 buffer）
    assert stt.transcribe_with_validation.called, \
        "Remaining buffer should be processed in finally block"
