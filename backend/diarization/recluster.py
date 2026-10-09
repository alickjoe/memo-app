"""
会后全局说话人重聚类

在线识别允许出错（同一人被拆成多个 label / 两人被合并）；录音结束后用全量音频
做离线重聚类修正：
1. 读录音 WAV（16kHz mono PCM16，capture.py 持久化于 ~/.memo/recordings/）
2. 能量法扫描语音区间 → 切 2s 窗（步长 1s，重叠防切在换人点）
3. 批量声纹嵌入 → 平均链接层次聚类（Lance-Williams 增量，纯 numpy 无 sklearn）
   → 小簇（<3 窗）按 margin 规则并入大簇或弃权
4. 簇按首次出现时间重命名 Speaker A/B/C...
5. 按时间重叠多数票映射回 transcript_segments（仅当前最新 version），重写 speaker
6. 失败静默降级（记日志，不影响会议收尾流程）

算法说明：不做顺序贪心分配（早期错误质心会级联污染），直接在全量成对相似度矩阵上
做标准平均链接 AHC；跨换人点的混合窗与两簇相似度都中等，平均链接下自然沉到
小簇/单例，由小簇归属规则兜底。门限在真实会议音频上标定（同人 0.75-0.88 /
异人 0.42-0.64，MERGE=0.64-0.66 簇数最稳定）。
"""
import asyncio
import logging
import os
import struct
import wave

import numpy as np

from diarization.embedding import SAMPLING_RATE

logger = logging.getLogger("memo.diarization")

WINDOW_SEC = 2.0        # 声纹窗口时长（1.6s partial 的可靠下限之上）
STEP_SEC = 1.0          # 窗滑动步长（1s 重叠；换人点两侧总有纯窗锚定簇）
MIN_REGION_SEC = 1.2    # 短于此的语音区间不参与聚类（1.2s 即可产出 1 个可靠 partial）
MIN_WINDOW_SEC = 1.2    # 短于此的尾窗丢弃
MAX_WINDOWS = 3000      # 长音频自适应拉大步长，控制相似度矩阵内存（3000² ≈ 36MB）
MERGE_SIM = 0.66        # AHC 平均链接合并门限（真实音频标定，0.64-0.66 簇数稳定）
ASSIGN_SIM = 0.70       # 小簇/单例并入大簇的质心相似度门限
SMALL_MARGIN = 0.08     # 小簇归属的最优/次优最小差值
MIN_CLUSTER_WINDOWS = 3 # 少于此窗数的簇不视为独立说话人（并入或弃权）


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
    # 长音频自适应步长：控制窗数上限（相似度矩阵 O(n²) 内存）
    total_speech = sum(e - s for s, e in regions) / SAMPLING_RATE
    step_sec = max(STEP_SEC, total_speech / MAX_WINDOWS)
    step_size = int(step_sec * SAMPLING_RATE)
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
    """标准平均链接层次聚类（Lance-Williams 增量）+ 小簇归属。

    不做顺序贪心分配：早期错误质心会级联污染（真实音频实测覆盖率仅 22%）。
    直接在全量成对相似度矩阵上 AHC，混合窗（跨换人点）与两簇的相似度都中等，
    平均链接下自然沉到小簇/单例，由小簇归属规则兜底。

    门限在真实会议音频上标定：同人窗对 0.75-0.88 / 异人 0.42-0.64，
    MERGE_SIM=0.64-0.66 时簇数最稳定。

    Returns:
        每窗的临时簇 label（"c0"、"c1"...）；弃权窗为 ""（不参与落库投票）。
    """
    n = len(embeddings)
    if n == 0:
        return []

    S = embeddings @ embeddings.T  # L2 归一化向量的内积即余弦相似度
    U = S.copy()
    np.fill_diagonal(U, -2.0)
    sizes = np.ones(n)
    members: list[list[int]] = [[i] for i in range(n)]
    alive = np.ones(n, dtype=bool)

    while True:
        idx = np.nonzero(alive)[0]
        if len(idx) < 2:
            break
        sub = U[np.ix_(idx, idx)]
        p = np.unravel_index(np.argmax(sub), sub.shape)
        i, j = int(idx[p[0]]), int(idx[p[1]])
        if float(U[i, j]) < MERGE_SIM:
            break
        # Lance-Williams 平均链接增量更新（精确，无需重算）
        U[i, :] = (sizes[i] * U[i, :] + sizes[j] * U[j, :]) / (sizes[i] + sizes[j])
        U[:, i] = U[i, :]
        U[i, i] = -2.0
        sizes[i] += sizes[j]
        members[i] = members[i] + members[j]
        members[j] = []
        alive[j] = False
        U[j, :] = -2.0
        U[:, j] = -2.0

    big = [m for m in members if len(m) >= MIN_CLUSTER_WINDOWS]
    small = [m for m in members if 0 < len(m) < MIN_CLUSTER_WINDOWS]

    labels = [""] * n
    for cid, m in enumerate(big):
        for i in m:
            labels[i] = f"c{cid}"

    if big and small:
        cents = np.stack([embeddings[m].mean(axis=0) for m in big])
        cents /= (np.linalg.norm(cents, axis=1, keepdims=True) + 1e-9)
        for m in small:
            for i in m:
                sims = cents @ embeddings[i]
                order = np.argsort(sims)[::-1]
                best = float(sims[order[0]])
                second = float(sims[order[1]]) if len(order) > 1 else -1.0
                if best >= ASSIGN_SIM and (best - second) >= SMALL_MARGIN:
                    labels[i] = f"c{int(order[0])}"
    elif small:
        # 只有小簇（如单窗会议）→ 保留第一个小簇避免全部弃权
        labels[:] = ""
        for i in small[0]:
            labels[i] = "c0"

    return labels


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

    # 簇按"最早窗开始时间"排序 → Speaker A/B/C...（弃权窗不参与命名与投票）
    cluster_ids = sorted(
        {l for l in set(window_labels) if l},
        key=lambda c: _min_time(c, windows, window_labels),
    )
    rename = {c: f"Speaker {chr(65 + k % 26)}" for k, c in enumerate(cluster_ids)}

    changed = 0
    for seg_id, old_speaker, seg_start, seg_end in rows:
        seg_start, seg_end = float(seg_start), float(seg_end)
        # 与该段时间重叠的窗多数票（票权=重叠时长；弃权窗不计票）
        votes: dict[str, float] = {}
        for (w_start, w_end), label in zip(windows, window_labels):
            if not label:
                continue
            overlap = min(seg_end, w_end) - max(seg_start, w_start)
            if overlap > 0:
                votes[label] = votes.get(label, 0.0) + overlap
        if not votes:
            continue  # 无有效覆盖窗（如极短段），保留在线识别标签
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
