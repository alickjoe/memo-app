"""
会后全局说话人重聚类

在线识别允许出错（同一人被拆成多个 label / 两人被合并）；录音结束后用全量音频
做离线重聚类修正：
1. 读录音 WAV（16kHz mono PCM16，capture.py 持久化于 ~/.memo/recordings/）
2. 能量法扫描语音区间 → 切 3s 窗（步长 2s，重叠防切在换人点）
3. 批量声纹嵌入 → leader 聚类 + 平均链接凝聚合并（纯 numpy，无 sklearn）
4. 簇按首次出现时间重命名 Speaker A/B/C...
5. 按时间重叠多数票映射回 transcript_segments（仅当前最新 version），重写 speaker
6. 失败静默降级（记日志，不影响会议收尾流程）
"""
import asyncio
import logging
import os
import struct
import wave

import numpy as np

from diarization.embedding import SAMPLING_RATE

logger = logging.getLogger("memo.diarization")

WINDOW_SEC = 2.0        # 声纹窗口时长（1.6s partial 的可靠下限之上，纯窗概率高）
STEP_SEC = 1.0          # 窗滑动步长（1s 重叠；换人点两侧总有纯窗锚定簇）
MIN_REGION_SEC = 2.0    # 短于此的语音区间不参与聚类
MIN_WINDOW_SEC = 2.0    # 短于此的尾窗丢弃
ASSIGN_SIM = 0.80       # 纯窗分配门限：与簇质心相似度达到才算"干净归属"
CREATE_SIM = 0.62       # 建新簇门限：与所有簇都低于此值 → 视为新说话人；
                        # 介于两者之间（0.62~0.80）的窗视为混合窗/不确定窗 → 弃权
PASS2_MARGIN = 0.08     # 第二遍复评的最优/次优最小差值（防混合窗误归）
MERGE_SIM = 0.75        # 凝聚合并的平均链接相似度门限


def recordings_path(meeting_id: str) -> str:
    """会议录音 WAV 路径（capture.py 的写入规则）"""
    data_dir = os.environ.get("DATA_DIR", os.path.expanduser("~/.memo"))
    return os.path.join(data_dir, "recordings", f"{meeting_id}.wav")


async def recluster_meeting(meeting_id: str, db, diarizer, audio_path: str | None = None) -> list[str]:
    """对该会议的全量录音重聚类并重写 transcript_segments 的 speaker。

    Returns:
        最终说话人 label 列表（按首次出现排序）；无法执行时返回空列表。
    """
    try:
        if not (diarizer and diarizer.engine.available):
            logger.info("Recluster skipped: voiceprint engine unavailable (meeting %s)", meeting_id)
            return []

        path = audio_path or recordings_path(meeting_id)
        if not path or not os.path.exists(path):
            logger.info("Recluster skipped: recording not found at %s (meeting %s)", path, meeting_id)
            return []

        # CPU 密集部分放线程池，避免阻塞事件循环
        windows, embeddings = await asyncio.to_thread(
            _extract_embeddings, diarizer.engine, path,
        )
        if embeddings is None or len(windows) == 0:
            logger.info("Recluster skipped: no embeddings produced (meeting %s)", meeting_id)
            return []

        window_labels = await asyncio.to_thread(_cluster_windows, embeddings)
        new_speakers = await _apply_to_db(meeting_id, db, windows, window_labels)
        logger.info(
            "Recluster done (meeting %s): windows=%d, speakers=%s",
            meeting_id, len(windows), new_speakers,
        )
        return new_speakers
    except Exception as e:
        logger.warning("Recluster failed (meeting %s): %s", meeting_id, e)
        return []


# ==================== 特征提取 ====================


def _extract_embeddings(engine, path: str):
    """读 WAV → 语音区间 → 声纹窗。

    Returns:
        (windows: [(start_sec, end_sec)], embeddings 矩阵 或 None)
    """
    wav = _load_wav(path)
    if wav is None:
        return [], None

    regions = _speech_regions(wav)
    if not regions:
        return [], None

    windows = []   # [(start_sec, end_sec)] 绝对时间
    wavs = []      # 对应 float32 波形
    win_size = int(WINDOW_SEC * SAMPLING_RATE)
    step_size = int(STEP_SEC * SAMPLING_RATE)
    for region_start, region_end in regions:
        region = wav[region_start:region_end]
        if len(region) < int(MIN_REGION_SEC * SAMPLING_RATE):
            continue
        region_t0 = region_start / SAMPLING_RATE
        offset = 0
        while offset < len(region):
            chunk = region[offset:offset + win_size]
            if len(chunk) >= int(MIN_WINDOW_SEC * SAMPLING_RATE):
                t0 = region_t0 + offset / SAMPLING_RATE
                windows.append((t0, t0 + len(chunk) / SAMPLING_RATE))
                wavs.append(chunk)
            offset += step_size

    if not wavs:
        return [], None

    batch = engine.embed_batch(wavs)
    if batch is None:
        return [], None
    embeddings, valid_idx = batch
    if len(valid_idx) == 0:
        return [], None
    # 过短窗已被 embed_batch 剔除，同步过滤时间区间
    windows = [windows[i] for i in valid_idx]
    return windows, embeddings


def _load_wav(path: str) -> np.ndarray | None:
    """读 16kHz mono PCM16 WAV → float32 [-1, 1]"""
    try:
        with wave.open(path, "rb") as wf:
            if wf.getframerate() != SAMPLING_RATE or wf.getnchannels() != 1:
                logger.warning(
                    "Recluster: unexpected WAV format %s (rate=%d, ch=%d)",
                    path, wf.getframerate(), wf.getnchannels(),
                )
            frames = wf.readframes(wf.getnframes())
    except Exception as e:
        logger.warning("Recluster: cannot read WAV %s: %s", path, e)
        return None
    n = len(frames) // 2
    if n < int(MIN_REGION_SEC * SAMPLING_RATE):
        return None
    samples = struct.unpack(f"<{n}h", frames[: n * 2])
    return np.array(samples, dtype=np.float32) / 32768.0


def _speech_regions(wav: np.ndarray) -> list[tuple[int, int]]:
    """能量法扫描语音区间（返回样本索引区间）。

    简化说明：不复用流式 Silero VAD 实例（其滞回状态机面向实时流），
    离线全量扫描用平滑能量门限即可可靠分离"有人说话/无人"区间。
    """
    win = int(0.03 * SAMPLING_RATE)  # 30ms 帧
    n_frames = len(wav) // win
    if n_frames == 0:
        return []
    frames = wav[: n_frames * win].reshape(n_frames, win)
    rms = np.sqrt((frames ** 2).mean(axis=1))

    peak = float(rms.max())
    if peak < 1e-4:
        return []
    thr = peak * 10 ** (-38 / 20)  # 相对峰值 -38dB

    flags = (rms > thr).astype(np.float32)
    # 平滑（约 270ms 窗），避免瞬时噪声打碎区间
    kernel = np.ones(9) / 9
    voiced = np.convolve(flags, kernel, mode="same") >= 0.5

    regions: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            regions.append((start * win, i * win))
            start = None
    if start is not None:
        regions.append((start * win, len(wav)))
    return regions


# ==================== 聚类 ====================


def _cluster_windows(embeddings: np.ndarray) -> list[str]:
    """两遍聚类，专门处理跨换人点的混合窗。

    混合窗（窗内含两人）的嵌入落在两簇之间，若强制归属会把所有簇"桥接"合并。
    策略：不确定的窗弃权（不产生标签，也不污染簇质心）。

    阶段 1（建簇）：逐窗扫描——
      - 无簇 → 建新簇
      - best >= ASSIGN_SIM → 归属（并更新质心均值）
      - best < CREATE_SIM → 建新簇（明显的新说话人）
      - 中间地带 → 弃权（可能是混合窗，也可能是相近音色，等阶段 2 复评）
    阶段 2（复评弃权窗，用完整簇集）：best >= ASSIGN_SIM 且 margin >= PASS2_MARGIN
      → 归属；否则保持弃权。
    阶段 3：平均链接凝聚合并（只合并不确定窗之外的簇）。

    Returns:
        每窗的临时簇 label；弃权窗为 ""（空串，不参与落库投票）。
    """
    n = len(embeddings)
    cluster_ids = np.full(n, -1, dtype=int)  # -1 = 弃权
    centroids: list[np.ndarray] = []
    counts: list[int] = []

    def assign(i: int, cid: int) -> None:
        cluster_ids[i] = cid
        c = centroids[cid]
        centroids[cid] = (c * counts[cid] + embeddings[i]) / (counts[cid] + 1)
        counts[cid] += 1

    # ---- 阶段 1 ----
    for i in range(n):
        if not centroids:
            centroids.append(embeddings[i].copy())
            counts.append(1)
            cluster_ids[i] = 0
            continue
        sims = np.stack(centroids) @ embeddings[i]
        order = np.argsort(sims)[::-1]
        best, second = float(sims[order[0]]), (float(sims[order[1]]) if len(order) > 1 else -1.0)
        if best >= ASSIGN_SIM:
            assign(i, int(order[0]))
        elif best < CREATE_SIM:
            centroids.append(embeddings[i].copy())
            counts.append(1)
            cluster_ids[i] = len(centroids) - 1
        # else: 弃权

    # ---- 阶段 2：复评弃权窗 ----
    for i in range(n):
        if cluster_ids[i] != -1 or not centroids:
            continue
        sims = np.stack(centroids) @ embeddings[i]
        order = np.argsort(sims)[::-1]
        best, second = float(sims[order[0]]), (float(sims[order[1]]) if len(order) > 1 else -1.0)
        if best >= ASSIGN_SIM and (best - second) >= PASS2_MARGIN:
            assign(i, int(order[0]))

    # ---- 阶段 3：凝聚合并 ----
    members: list[list[int]] = [[] for _ in range(len(centroids))]
    for i, cid in enumerate(cluster_ids):
        if cid >= 0:
            members[cid].append(i)
    members = _agglomerate(members, embeddings)

    labels = [""] * n
    for new_id, member in enumerate(members):
        for i in member:
            labels[i] = f"c{new_id}"
    return labels


def _agglomerate(members: list[list[int]], embeddings: np.ndarray) -> list[list[int]]:
    """平均链接凝聚合并：反复合并平均链接相似度 >= MERGE_SIM 的最相似簇对

    质心均值作为平均链接的近似（各窗等权、L2 归一化向量均值后再归一化）。
    """
    members = [m for m in members if m]
    def centroid_of(member: list[int]) -> np.ndarray:
        c = embeddings[member].mean(axis=0)
        norm = np.linalg.norm(c, 2)
        return c / norm if norm > 0 else c

    while len(members) > 1:
        cents = np.stack([centroid_of(m) for m in members])
        sims = cents @ cents.T
        np.fill_diagonal(sims, -2.0)
        i, j = np.unravel_index(np.argmax(sims), sims.shape)
        if float(sims[i, j]) < MERGE_SIM:
            break
        members[min(i, j)] = members[i] + members[j]
        members[max(i, j)] = []
        members = [m for m in members if m]
    return members


# ==================== 落库 ====================


async def _apply_to_db(meeting_id: str, db, windows, window_labels: list[str]) -> list[str]:
    """把窗级簇 label 按时间重叠多数票映射到 transcript_segments 行并重写。

    Returns:
        重命名后的说话人列表（按首次出现时间排序）。
    """
    cur = await db.execute(
        "SELECT MAX(version) FROM transcript_segments WHERE meeting_id = ?",
        (meeting_id,),
    )
    row = await cur.fetchone()
    max_version = row[0] if row and row[0] is not None else 1

    cur = await db.execute(
        "SELECT id, speaker, start_time, end_time FROM transcript_segments "
        "WHERE meeting_id = ? AND version = ? ORDER BY start_time",
        (meeting_id, max_version),
    )
    rows = await cur.fetchall()
    if not rows:
        return []

    # 簇按"最早窗开始时间"排序 → Speaker A/B/C...
    cluster_ids = sorted(set(window_labels), key=lambda c: _min_time(c, windows, window_labels))
    rename = {c: f"Speaker {chr(65 + k % 26)}" for k, c in enumerate(cluster_ids)}

    changed = 0
    for seg_id, old_speaker, seg_start, seg_end in rows:
        seg_start, seg_end = float(seg_start), float(seg_end)
        # 与该段时间重叠的窗多数票（票权=重叠时长）
        votes: dict[str, float] = {}
        for (w_start, w_end), label in zip(windows, window_labels):
            overlap = min(seg_end, w_end) - max(seg_start, w_start)
            if overlap > 0:
                votes[label] = votes.get(label, 0.0) + overlap
        if not votes:
            continue  # 无覆盖窗（如极短段），保留在线识别标签
        new_label = rename[max(votes, key=votes.get)]
        if old_speaker != new_label:
            await db.execute(
                "UPDATE transcript_segments SET speaker = ? WHERE id = ?",
                (new_label, seg_id),
            )
            changed += 1
    await db.commit()
    logger.info(
        "Recluster: rewrote %d/%d segments (meeting %s)", changed, len(rows), meeting_id,
    )
    return [rename[c] for c in cluster_ids]


def _min_time(cluster: str, windows, window_labels: list[str]) -> float:
    """某簇所有窗的最早开始时间"""
    return min(t[0] for t, label in zip(windows, window_labels) if label == cluster)
