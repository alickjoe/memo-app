"""
说话人识别（声纹嵌入）单元测试

不依赖 torch / resemblyzer：用 FakeVoiceprintEngine 注入受控向量，
覆盖在线聚类双门限逻辑、降级路径、重聚类全流程。
"""
import os
import wave
from unittest.mock import MagicMock

import numpy as np
from diarization.embedding import SAMPLING_RATE
from diarization.recluster import (
    _apply_to_db,
    _cluster_windows,
    _speech_regions,
    recluster_meeting,
)
from diarization.speaker import SpeakerDiarizer

# ==================== Fake 引擎 ====================


class FakeVoiceprintEngine:
    """受控声纹引擎：按注册表返回向量（忽略真实音频内容）"""

    def __init__(self, registry: dict[str, np.ndarray] | None = None,
                 unavailable: bool = False):
        self._registry = registry or {}
        self.unavailable = unavailable
        self.load_error: str | None = "not loaded" if unavailable else None

    @property
    def available(self) -> bool:
        return not self.unavailable

    def load(self) -> bool:
        return not self.unavailable

    def embed(self, pcm16_bytes: bytes) -> np.ndarray | None:
        """按音频长度取模从注册表轮询（测试通过长度控制身份）"""
        if not self._registry:
            return None
        keys = sorted(self._registry.keys())
        idx = (len(pcm16_bytes) // (SAMPLING_RATE * 2)) % len(keys)
        return self._registry[keys[idx]]

    def embed_batch(self, windows: list[np.ndarray]) -> np.ndarray | None:
        if not windows:
            return None
        return np.stack([
            np.abs(np.sin(w[:100]).sum()) * np.ones(4, dtype=np.float32) for w in windows
        ])


def make_pcm(seconds: float) -> bytes:
    """任意时长的伪 PCM（内容不重要，Fake 引擎只看长度）"""
    return b"\x01\x02" * int(SAMPLING_RATE * seconds)


# ==================== 在线聚类逻辑 ====================


def make_diarizer(**kwargs) -> SpeakerDiarizer:
    d = SpeakerDiarizer(engine=MagicMock())
    d._centroids.clear()
    if kwargs:
        d.configure(**kwargs)
    return d


def test_assign_empty_creates_new():
    d = make_diarizer()
    label = d._assign(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32))
    assert label == "Speaker A"
    assert d.speaker_count == 1


def test_assign_hit_existing_high_similarity():
    d = make_diarizer()
    v0 = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    d._assign(v0)
    # 高相似向量（cos ≈ 0.995 > 0.70，且无次优 → margin 通过）
    label = d._assign(np.array([0.995, 0.1, 0.0, 0.0], dtype=np.float32) /
                      np.linalg.norm([0.995, 0.1, 0.0, 0.0]))
    assert label == "Speaker A"
    assert d.speaker_count == 1


def test_assign_threshold_miss_creates_new():
    d = make_diarizer()
    d._assign(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32))
    # 正交向量 cos=0 < 0.70 → 新建
    label = d._assign(np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32))
    assert label == "Speaker B"
    assert d.speaker_count == 2


def test_assign_margin_insufficient_creates_new():
    d = make_diarizer(match_threshold=0.5, margin=0.5)
    d._assign(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32))
    d._assign(np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32))
    # 对 A、B 的相似度都 ≈ 0.7：超过阈值但 margin 不足 → 新建
    v = np.array([1.0, 1.0, 0.0, 0.0], dtype=np.float32) / np.sqrt(2)
    label = d._assign(v)
    assert label == "Speaker C"


def test_assign_over_limit_forces_nearest():
    d = make_diarizer(max_speakers=1)
    v0 = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    d._assign(v0)
    # 正交但超上限 → 强制归入最近（唯一）簇
    label = d._assign(np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32))
    assert label == "Speaker A"
    assert d.speaker_count == 1


def test_identify_success_returns_label():
    eng = FakeVoiceprintEngine(registry={"v1": np.array([1.0, 0.0], dtype=np.float32)})
    d = SpeakerDiarizer(engine=eng)
    label = _run(d.identify(make_pcm(2.0)))
    assert label == "Speaker A"


def test_identify_unavailable_returns_none():
    eng = FakeVoiceprintEngine(unavailable=True)
    d = SpeakerDiarizer(engine=eng)
    assert _run(d.identify(make_pcm(2.0))) is None


def test_identify_short_segment_returns_none():
    eng = FakeVoiceprintEngine(registry={"v1": np.ones(4, dtype=np.float32)})
    d = SpeakerDiarizer(engine=eng)
    # FakeEngine 注册表按长度取模：此处直接让 embed 返回 None
    eng.embed = lambda pcm: None
    assert _run(d.identify(make_pcm(2.0))) is None


def test_identify_disabled_returns_none():
    eng = FakeVoiceprintEngine(registry={"v1": np.ones(4, dtype=np.float32)})
    d = SpeakerDiarizer(engine=eng)
    d.configure(enabled=False)
    assert _run(d.identify(make_pcm(2.0))) is None


def test_configure_applies_values():
    d = make_diarizer(match_threshold=0.8, margin=0.2, max_speakers=3)
    assert d.match_threshold == 0.8
    assert d.margin == 0.2
    assert d.max_speakers == 3


def test_reset_clears_state():
    d = make_diarizer()
    d._assign(np.ones(4, dtype=np.float32))
    d.reset()
    assert d.speaker_count == 0
    assert d._next_speaker_id == 0


# ==================== 重聚类 ====================


def test_cluster_windows_separates_two_voices():
    """同一身份的窗（加微噪）应聚到一起，两个身份分开"""
    rng = np.random.default_rng(42)
    base_a = np.zeros(8, dtype=np.float32); base_a[0] = 1.0
    base_b = np.zeros(8, dtype=np.float32); base_b[1] = 1.0
    vecs = []
    for base in (base_a, base_b):
        for _ in range(6):
            v = base + rng.normal(0, 0.01, 8).astype(np.float32)
            vecs.append(v / np.linalg.norm(v))
    embeddings = np.stack(vecs)
    labels = _cluster_windows(embeddings)
    # 窗 0-5 与窗 6-11 各成一簇
    assert labels[0] == labels[3] == labels[5]
    assert labels[6] == labels[10]
    assert labels[0] != labels[6]


def test_speech_regions_two_regions():
    """中段有语音、前后静音 → 恰好一个语音区间"""
    sr = SAMPLING_RATE
    wav = np.zeros(sr * 6, dtype=np.float32)  # 6s 静音
    wav[2 * sr:4 * sr] = 0.3 * np.sin(np.linspace(0, 400 * np.pi, 2 * sr)).astype(np.float32)
    regions = _speech_regions(wav)
    assert len(regions) == 1
    start, end = regions[0]
    assert start / sr <= 2.2
    assert end / sr >= 3.8


def test_apply_to_db_rewrites_and_renames_by_time(test_db):
    """窗级标签应按时间重叠多数票写回 DB，簇按首现时间重命名 A/B"""

    async def setup():
        await test_db.execute(
            "INSERT INTO meetings (id, title, status) VALUES (?, ?, ?)", ("m1", "T", "done"),
        )
        # 段 1 在 0-5s（对应 c1），段 2 在 10-15s（对应 c2），在线标签是错的
        await test_db.execute(
            "INSERT INTO transcript_segments (meeting_id, speaker, start_time, end_time, text) "
            "VALUES (?, ?, ?, ?, ?)", ("m1", "Speaker C", 0.0, 5.0, "hello"),
        )
        await test_db.execute(
            "INSERT INTO transcript_segments (meeting_id, speaker, start_time, end_time, text) "
            "VALUES (?, ?, ?, ?, ?)", ("m1", "Speaker C", 10.0, 15.0, "world"),
        )
        await test_db.commit()

    _run(setup())

    windows = [(0.0, 3.0), (2.0, 5.0), (4.0, 7.0), (10.0, 13.0), (12.0, 15.0)]
    labels = ["c1", "c1", "c1", "c2", "c2"]
    speakers = _run(_apply_to_db("m1", test_db, windows, labels))
    assert speakers == ["Speaker A", "Speaker B"]  # c1 先出现 → A

    async def fetch_rows():
        cur = await test_db.execute(
            "SELECT speaker, start_time FROM transcript_segments ORDER BY start_time"
        )
        return await cur.fetchall()

    rows = _run(fetch_rows())
    assert rows[0][0] == "Speaker A"
    assert rows[1][0] == "Speaker B"


def test_recluster_meeting_skips_without_engine(test_db):
    d = SpeakerDiarizer(engine=FakeVoiceprintEngine(unavailable=True))
    assert _run(recluster_meeting("m1", test_db, d)) == []


def test_recluster_meeting_end_to_end(test_db, temp_data_dir):
    """合成 WAV → 重聚类 → 标签重写"""

    async def setup():
        await test_db.execute(
            "INSERT INTO meetings (id, title, status) VALUES (?, ?, ?)", ("m2", "T", "done"),
        )
        for i in range(3):
            await test_db.execute(
                "INSERT INTO transcript_segments (meeting_id, speaker, start_time, end_time, text) "
                "VALUES (?, ?, ?, ?, ?)", ("m2", "Speaker A", float(i * 4), float(i * 4 + 4), f"t{i}"),
            )
        await test_db.commit()

    _run(setup())

    # 写一个 12s 的伪 WAV（内容不重要，FakeEngine.embed_batch 兜底）
    path = os.path.join(temp_data_dir, "recordings")
    os.makedirs(path, exist_ok=True)
    wav_path = os.path.join(path, "m2.wav")
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLING_RATE)
        wf.writeframes(b"\x01\x02" * SAMPLING_RATE * 12)

    eng = FakeVoiceprintEngine()
    d = SpeakerDiarizer(engine=eng)
    speakers = _run(recluster_meeting("m2", test_db, d))
    # FakeEngine 向量与内容无关，簇数不确定，但流程必须成功且标签以 Speaker 开头
    assert all(s.startswith("Speaker ") for s in speakers)


def test_recluster_meeting_missing_file(test_db, temp_data_dir):
    eng = FakeVoiceprintEngine()
    d = SpeakerDiarizer(engine=eng)
    assert _run(recluster_meeting("no-such-meeting", test_db, d)) == []


# ==================== utils ====================


def _run(coro):
    """在同步测试中运行协程"""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
