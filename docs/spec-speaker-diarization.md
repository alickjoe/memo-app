# Spec：基于声纹嵌入（音色）的说话人识别重构

状态：**已确认并实施**（2026-10-09 用户确认四项：Resemblyzer 选型 / 开启重聚类 / Phase 2 暂缓 / spec 其余内容）

实施过程中的关键调整（相对原 spec）：
- mel 谱计算用 numpy FFT + torchaudio mel filterbank 复刻，**彻底不依赖 librosa/scipy**（比原计划的 librosa wheel 更干净）；
- webrtcvad 桩扩展为三件套（webrtcvad / librosa / scipy.ndimage.morphology），因为 resemblyzer 包 `__init__` 的 import 链三者都会触发；
- `identify()` 嵌入计算放 `asyncio.to_thread`，避免 CPU 推理阻塞事件循环；
- 重聚类批量嵌入用整批共享 mel 配置的分块前向（每块 128 partial），比逐窗调用 embed_utterance 快数倍；
- PyInstaller 打包显式 `--exclude-module torch/resemblyzer/...`，frozen exe 保持原体积（frozen 模式走降级路径）；
- Windows 自动安装链路：dev.ps1 与 electron python-bridge 的 torch 安装流程中加入容错 Step（`pip install --no-deps Resemblyzer==0.1.4`，失败仅降级不阻塞）；
- requirements.txt 在 win32 marker 下装 torch+torchaudio（CI Linux 不装，不拖慢 lint workflow）。


涉及范围：`backend/`（Python 后端）为主；前端仅小改（可选）

---

## 1. 问题分析（现状为什么不准）

当前实现 `backend/diarization/speaker.py`：

```python
# 特征: [RMS能量, 过零率, 频谱质心近似, 峰度]
rms = np.sqrt(np.mean(samples_np ** 2))
zcr = np.sum(np.abs(np.diff(np.sign(samples_np)))) / (2 * len(samples_np))
spectral_centroid = np.sum(np.abs(np.diff(samples_np))) / len(samples_np)
kurtosis = ...
```

| # | 缺陷 | 说明 |
|---|------|------|
| 1 | **特征无判别力（根因）** | RMS=音量、过零率=高频噪声占比、峰度=波形尖锐度——这些量主要由「麦克风距离 / 音量设置 / 背景噪声 / 说话内容」决定，与"是谁在说"几乎无关。同一个声音小声说话就会被判成另一个人 |
| 2 | 欧氏距离 + 原始量纲 | 4 个特征量纲和方差差异巨大，硬阈值 2.0 无统计意义 |
| 3 | EMA 漂移 | 0.7/0.3 移动平均会把不同说话人的"质心"逐渐互相拉拢 |
| 4 | 上限强制合并 | 满 4 人后强制归入最相似者，错误持续传播 |
| 5 | 无全局一致性 | 每段独立在线判定，同一人中途音量/内容变化就会被拆成两个 label，之后无法纠正 |

真正的"音色"判别靠的是**说话人声纹嵌入**（speaker embedding）：用神经网络把一段语音编码成几百维向量，同一个人的向量距离小、不同人大。这是当前学界/工业界 diarization 的标准做法。

---

## 2. 方案总览

三层结构，逐层递进：

```
┌─ 第 1 层：声纹嵌入引擎（替换现在的手工特征）
│    每个语音段 → 256 维 d-vector（预训练 GE2E LSTM，CPU 推理）
│
├─ 第 2 层：在线增量聚类（实时转写时）
│    余弦相似度 + 双门限匹配 + 多说话人质心维护
│
└─ 第 3 层：会后全局重聚类（录音结束后，质量兜底）
     读已保存的录音 WAV → Silero VAD 精细切片 → 全量嵌入
     → 凝聚层次聚类 → 统一重写该会议所有说话人标签 → WebSocket 推送
```

第 3 层是关键增值：在线判定允许出错，会后用全量数据做全局聚类修正（解决"同一人被拆成两个 label / 两人被合并"），用户在会议详情页直接看到修正后的结果。

---

## 3. 技术选型

| 方案 | 嵌入质量 | 部署风险（Windows 桌面端 / 公司网络） | 结论 |
|------|---------|------|------|
| **A. Resemblyzer（GE2E d-vector, 256 维）** | 好（VoxCeleb1 verification 可用级） | 模型 `pretrained.pt` (~17MB) **随 wheel 内置，运行时零下载**；torch 在源码模式下已随 Silero VAD 安装；唯一坑是 `webrtcvad` 依赖在 Windows 需编译（见 §6 规避方案） | ✅ **推荐（Phase 1）** |
| B. SpeechBrain ECAPA-TDNN（192 维） | 更好（EER ~0.7% 量级） | 模型 ~80MB 需首次运行时从 HuggingFace 下载，公司 TLS 网关下有不确定性；speechbrain 依赖树重 | 备选（Phase 3 可作引擎切换项） |
| C. ONNX WeSpeaker / CAM++ | 好 | 无 torch 也能跑（frozen exe 也可用），但需自备 ~28MB ONNX 模型文件 + onnxruntime；需额外打包资产工作 | 备选（若希望 frozen 模式也支持声纹） |
| D. pyannote.audio 端到端 diarization | 最好 | 模型 HF gated 需 token，依赖重，离线整段处理模式与本项目流式分段架构冲突 | 不采用 |
| E. 现状手工特征 | 无判别力 | — | 废弃 |

**推荐 A** 的核心理由：
- 与现有架构一致：源码模式已装 torch + torchaudio（Silero VAD 同款链路），零新增运行时下载；
- `pip install Resemblyzer` 的 wheel 内置预训练模型，规避公司网络运行时下载风险；
- CPU 推理快（每段 ~100ms 量级），逐段异步执行不影响实时转写节奏；
- Apache-2.0 许可，可安全商用。

**降级矩阵**（与现有 Silero VAD 降级模式对齐）：

| 运行条件 | 行为 |
|---------|------|
| torch 可用（源码模式主路径） | 声纹识别完整可用 |
| torch 不可用（frozen exe） | 声纹引擎加载失败 → 显式标记 degraded，退回"顺序标签（Speaker A/B/…）"，WS 推送一次提示事件，UI 可见提示，不崩溃 |
| 单段语音 < 1.0s | 该段不独立判声纹（过短不可靠），沿用前一段的说话人标签 |

---

## 4. 详细设计

### 4.1 新文件 `backend/diarization/embedding.py` — 声纹引擎封装

```python
class VoiceprintEngine:
    """Resemblyzer GE2E 声纹嵌入封装，含预处理与降级管理"""
    def load(self) -> bool            # 懒加载；失败 → self.available=False（不抛异常）
    def embed(self, pcm16_bytes) -> np.ndarray | None
        # 内部：int16 bytes → float32/32768 → 音量归一化(-30dBFS, 复用 resemblyzer
        # normalize_volume 逻辑) → 能量法裁剪首尾静音（不用 webrtcvad）
        # → VoiceEncoder.embed_utterance() → float32[256]（L2 归一化）
```

要点：
- **绕过 webrtcvad**：不用 resemblyzer 的 `trim_long_silences`（它依赖 webrtcvad），改用自带能量 VAD（复用现有 `audio/vad.py` 的能量逻辑）裁剪；对 ≥1s 的语音段，裁剪质量足够。
- 安装方式：`pip install Resemblyzer --no-deps` + `pip install librosa scipy`（二者在 Windows 均有预编译 wheel），并在 import 前注入 webrtcvad 桩模块防止其 import 链失败（详见 §6）。
- 模型加载失败（无 torch 等）→ `available=False`，上游走降级。

### 4.2 重写 `backend/diarization/speaker.py` — 在线增量聚类

```python
class SpeakerDiarizer:
    MATCH_THRESHOLD = 0.70    # 余弦相似度绝对门限（可配）
    MARGIN          = 0.10    # 最优与次优的差值门限（可配）
    MAX_SPEAKERS    = 6       # 上限（从 4 放宽，超限归最近簇且不再更新质心）
    EMA_ALPHA       = 0.20    # 质心更新速率

    def identify(self, segment_bytes) -> str | None
        # 1) embed 得到向量 v（不可用/过短 → None，调用方沿用前段标签或顺序标签）
        # 2) 与所有质心算 cosine
        # 3) best ≥ THRESHOLD 且 (best − second) ≥ MARGIN → 命中：
        #      质心 EMA 更新后返回该 label
        #    否则未满上限 → 新建 Speaker X（返回 label）
        #    否则（超上限）→ 归入最相似簇，返回其 label，不更新质心
```

要点：
- **接口变更**：`identify()` 成功时直接返回 label（含新建），仅"无法判定"时返回 None。修掉现在"diarizer 内部标签 / 调用方标签分别生成"的脆弱耦合。
- 每说话人维护**质心**（不再用裸 EMA 单向量逐步漂移）；命中才更新、未命中不更新，避免错误拉拢。
- 阈值可从 settings 覆盖（见 4.5）。

### 4.3 新文件 `backend/diarization/recluster.py` — 会后全局重聚类

```python
async def recluster_meeting(meeting_id, db) -> None
    # 1) 读录音 WAV：~/.memo/recordings/{meeting_id}.wav（librosa.load，自动重采样到 16k）
    # 2) Silero VAD（已加载实例，复用）取语音时间区间；不可用则能量法
    # 3) 语音区间切 1.5~3s 窗（带 0.2s 重叠），批量嵌入（每窗 <150ms）
    # 4) 贪心凝聚层次聚类（average-link，cosine 距离 = 1 − sim，距离阈值 = 1 − MATCH_THRESHOLD），
    #    纯 numpy 实现 ~60 行，不引入 sklearn
    # 5) 簇按"首次出现时间"排序 → 重命名为 Speaker A/B/C…（保证跨会议语义一致）
    # 6) 按时间区间映射回 transcript_segments：与每个段的重叠最长的簇即该段标签
    #    → UPDATE transcript_segments SET speaker=?
    # 7) WS 推送 {"type": "speakers_updated", "speakers": [...]}；无对应 WS 连接则跳过
```

要点：
- **不改数据库 schema**：录音 WAV 已持久化（`capture.py` 写 `~/.memo/recordings/{meeting_id}.wav`），会后重嵌入比存 per-segment BLOB 质量更高（可用更细粒度窗口），schema 零迁移。
- 触发点（三处）：
  1. `process_audio_pipeline()` 的 `finally` 收尾段保存之后（正常录音结束）；
  2. `_run_retranscribe_sync()` 存入新版本段之后（仅作用于最新 version 的段）；
  3. `process_imported_audio()` 处理完导入音频之后。
- 设置项 `diarization_recluster` 可关（关则跳过，保持在线标签）。
- retranscribe 版本处理：只重写该会议 `version = MAX(version)` 的行，历史版本不动。

### 4.4 `backend/main.py` 集成点（最小侵入）

- `_transcribe_segment()`：
  - `diarizer.identify(segment_bytes)` 返回 None 且**不是第一次段**时，沿用上一段的 speaker（短段/嵌入失败时标签连贯）；first segment 返回 None 才走顺序编号。
  - 现有 STT 调用、落库、WS 推送逻辑不变（`speaker` 字段格式不变，前端零改动即兼容）。
- `main.py` 启动时构建 `SpeakerDiarizer(VoiceprintEngine())`，diarizer 内部管理 engine 生命周期与降级。

### 4.5 配置项（settings 表 + `src/pages/Settings.tsx`）

| key | 默认 | 说明 |
|-----|------|------|
| `diarization_enabled` | `true` | 关闭后回退顺序标签 |
| `diarization_match_threshold` | `0.70` | 余弦命中门限（调高→更保守、更易分新人） |
| `diarization_margin` | `0.10` | 双门限差值 |
| `diarization_max_speakers` | `6` | 说话人上限 |
| `diarization_recluster` | `true` | 会后全局重聚类开关 |

前端：Settings 录音页追加「说话人识别」小节（引擎状态显示：可用 / 已降级原因）；`recording_*` 同款读写链路。

### 4.6 WebSocket 协议

- 新增事件（一次性）：`{"type": "speakers_updated", "speakers": ["Speaker A", ...]}` → `src/pages/MeetingDetail.tsx` 收到后重新拉取该会议 transcript。
- 现有 `transcript` 事件格式不变。

### 4.7 前端改动（可选，默认不含在 Phase 1）

说话人 chip 改名（"Speaker A" → 真实姓名，按 meeting 存映射）与实时流颜色扩展，价值高但独立于识别准确率，建议 Phase 2 另行确认。

---

## 5. 性能与资源

| 项 | 量级 |
|----|------|
| 单段嵌入延迟 | ~100ms/CPU（每段一次，与 STT 网络调用可并行，非瓶颈） |
| 重聚类总耗时 | 1 小时录音 ≈ 全量窗口嵌入 ~1-2 分钟（后台线程执行，不阻塞 UI/STT） |
| 内存 | 模型 ~30MB + 每簇质心 256 floats |
| 磁盘 | 预训练模型 ~17MB（wheel 内置）；不落库 embedding |
| 网络依赖 | 安装时 pip（HTTPS，公司网关已验证可行）；运行时零下载 |

---

## 6. 风险与缓解

| # | 风险 | 缓解 |
|---|------|------|
| 1 | `webrtcvad` 在 Windows 无预编译 wheel，pip 源码编译需 MSVC | `pip install Resemblyzer --no-deps` + 只装 librosa/scipy（全 wheel），import 前注入 webrtcvad 桩模块（桩只满足 import 链，运行路径不触碰它）。**实施第一步先做 10 分钟依赖 spike 验证**（Debian 开发机 + pip 下载链路），失败则回退方案 C（ONNX） |
| 2 | 麦克风 + 系统回放混音（AudioMixer）：远端会议人声经扬声器→麦克风，与现场人声音色通道差异大 | Phase 1 按"一个混合通道"处理，文档标注已知限制；v2 可按音源分路分别聚类 |
| 3 | 相近音色（同性别、相近音高）误合并 | 双门限（margin）+ 会后全局重聚类兜底；阈值开放调整 |
| 4 | GE2E 英文语料训练，中文说话人效果 | 说话人判别跨语言可迁移，仍远好于现状；合成测试用例用中文参数生成验证；实测不达标再评估 ECAPA（选型 B 留了升级路径） |
| 5 | 重聚类改标签与用户预期冲突（用户已手动改过？） | Phase 1 无改名功能，无冲突面；Phase 2 引入改名后重聚类需跳过已改名段（届时设计） |
| 6 | frozen exe（无 torch）模式 | 降级矩阵明确：声纹不可用 → 顺序标签 + UI 提示，不崩溃不阻塞 |

---

## 7. 测试计划

- **单元测试**（`backend/tests/test_diarization.py`，pytest，遵循现有 asyncio_mode=auto）：
  - 合成音频：不同基频（85/120/180/220Hz）+ 不同共振峰滤波 + 轻噪声生成 2~4 人交替语音，断言在线聚类一致率与重聚类一致率阈值；
  - 双门限逻辑：同质心高相似命中 / 相似但 margin 不足 → 新建；
  - 短段沿用前段标签；超上限归最近簇不更新质心；
  - 降级：engine 不可用时 identify 返回 None 且 pipeline 正常完成；
  - recluster：合成 WAV 全流程 → 标签重写正确、WS 事件 payload 断言。
- **工程质量**：`cd backend && pytest`、`ruff check backend/`、`npm run precommit` 全绿（强制，见 AGENTS.md）。
- **人工验证**：双人真实对话录音，对比改造前后 speaker 标签正确性；重聚类前后对比。

### 验收标准

1. 合成 2 人交替音频：重聚类后标签一致率 ≥95%，在线一致率 ≥90%（现状基线 ~接近随机）；
2. 单段声纹处理延迟 <300ms（开发机 CPU）；
3. 无 torch 环境优雅降级，录音/转写主流程不受影响；
4. precommit + CI（frontend-lint / backend-lint / build）全通过。

---

## 8. 实施顺序与工作量

| 步骤 | 内容 | 估时 |
|------|------|------|
| 0 | 依赖 spike：pip 安装 Resemblyzer --no-deps + librosa/scipy + webrtcvad 桩 import 验证 + 模型加载 + 真实/合成音频嵌入冒烟 | 0.5h |
| 1 | `embedding.py` + `speaker.py` 重写 + 单测 | 2-3h |
| 2 | `main.py` 集成（短段沿用、降级提示 WS） | 1-2h |
| 3 | `recluster.py` + 三处触发点 + WS 事件 | 2h |
| 4 | Settings 前后端（5 个配置项 + 引擎状态显示） | 1h |
| 5 | 测试补全 + precommit + CI 验证 | 1h |

合计约 8-10h，可分 PR 交付（步骤 0-2 为 PR1 核心；3 为 PR2 重聚类；4-5 收尾）。

---

## 9. Phase 2 备选（本次不做，另行确认）

1. 声纹注册：预录 10s 自我介绍建立"已知说话人档案"，实时直接映射到真实姓名；
2. 说话人改名 UI + 按 meeting 存储映射；
3. ECAPA-TDNN 引擎切换项（设置里选引擎）；
4. 麦克风/系统回放双路独立声纹聚类。
