"""
说话人分离模块 - 基于声纹嵌入（d-vector）的在线增量聚类

匹配策略（双门限）：
- 与各说话人质心算余弦相似度，取最优 best 与次优 second
- best >= match_threshold 且 (best - second) >= margin → 命中既有说话人
- 否则说话人未满上限 → 新建
- 超上限 → 强制归入最相似簇（不更新质心，防错误拉拢）

降级策略：
- engine 未注入或加载失败（如 frozen exe 无 torch）→ identify() 返回 None，
  调用方回退顺序标签（Speaker A/B/...），主流程不受影响
"""
import asyncio
import logging

import numpy as np

from diarization.embedding import VoiceprintEngine

logger = logging.getLogger("memo.diarization")


class SpeakerDiarizer:
    """声纹嵌入 + 在线增量聚类的说话人分离器"""

    MAX_SPEAKERS = 6          # 说话人上限（默认值，可被 configure 覆盖）
    MATCH_THRESHOLD = 0.70    # 余弦相似度命中门限（默认值）
    MARGIN = 0.10             # 最优与次优的差值门限（默认值）
    EMA_ALPHA = 0.20          # 质心更新速率

    def __init__(self, engine: VoiceprintEngine | None = None):
        self.engine = engine or VoiceprintEngine()
        self._centroids: dict[str, np.ndarray] = {}
        self._next_speaker_id = 0
        # 实例级可配置参数（settings 覆盖类默认值）
        self.max_speakers = self.MAX_SPEAKERS
        self.match_threshold = self.MATCH_THRESHOLD
        self.margin = self.MARGIN
        # 总开关（diarization_enabled 设置项）
        self.enabled = True

    @property
    def worth_try(self) -> bool:
        """当前是否值得尝试声纹识别（enabled 且引擎未确认不可用）。

        available=True 或尚未尝试过加载（load_error 为 None）时为 True；
        加载已失败（如 frozen exe 无 torch）后恒为 False，调用方可直接走顺序标签。
        """
        return self.enabled and (self.engine.available or self.engine.load_error is None)

    # ---------- 配置 ----------

    def configure(
        self,
        match_threshold: float | None = None,
        margin: float | None = None,
        max_speakers: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        """运行时覆盖参数（来自 settings 表）"""
        if match_threshold is not None and match_threshold > 0:
            self.match_threshold = float(match_threshold)
        if margin is not None and margin >= 0:
            self.margin = float(margin)
        if max_speakers is not None and max_speakers >= 1:
            self.max_speakers = int(max_speakers)
        if enabled is not None:
            self.enabled = bool(enabled)

    # ---------- 识别 ----------

    async def identify(self, audio_bytes: bytes) -> str | None:
        """识别说话人。

        Returns:
            命中既有说话人或成功新建时返回其 label；
            引擎不可用 / 段过短 / 嵌入失败时返回 None（调用方沿用前段标签）。
        """
        try:
            if not self.worth_try:
                return None

            # 嵌入是 CPU 密集调用（~100ms/段），放线程池避免阻塞事件循环
            embed = await asyncio.to_thread(self.engine.embed, audio_bytes)
            if embed is None:
                return None

            return self._assign(embed)
        except Exception as e:
            logger.warning("Diarization failed: %s", e)
            return None

    def _assign(self, embed: np.ndarray) -> str | None:
        """双门限增量聚类（纯逻辑，供测试直接调用）"""
        ids = list(self._centroids.keys())
        if not ids:
            return self._create_speaker(embed)

        centroid_matrix = np.stack([self._centroids[sid] for sid in ids])
        sims = centroid_matrix @ embed  # L2 归一化向量的内积即余弦相似度
        order = np.argsort(sims)[::-1]
        best_idx, best = order[0], float(sims[order[0]])
        second = float(sims[order[1]]) if len(order) > 1 else -1.0

        if best >= self.match_threshold and (best - second) >= self.margin:
            sid = ids[best_idx]
            # 命中才更新质心（EMA），未命中不更新，避免错误拉拢
            self._centroids[sid] = self._centroids[sid] * (1 - self.EMA_ALPHA) \
                + embed * self.EMA_ALPHA
            return sid

        # 未命中：未满上限 → 新建；超上限 → 强制归入最相似簇（不更新质心）
        if len(ids) >= self.max_speakers:
            return ids[best_idx]
        return self._create_speaker(embed)

    def _create_speaker(self, embed: np.ndarray) -> str:
        """新建说话人，返回其 label"""
        self._next_speaker_id += 1
        sid = f"Speaker {chr(65 + (self._next_speaker_id - 1) % 26)}"
        self._centroids[sid] = embed
        logger.debug("Diarization: new speaker %s (total=%d)", sid, self._next_speaker_id)
        return sid

    # ---------- 状态 ----------

    @property
    def speaker_count(self) -> int:
        return len(self._centroids)

    def reset(self):
        """重置说话人记录（每次录音开始时调用）"""
        self._centroids.clear()
        self._next_speaker_id = 0
