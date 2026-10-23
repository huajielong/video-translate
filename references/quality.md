# 质量策略

## 字幕

- 翻译阶段先按标点把超长块拆到 ≤28 字（`_split_long_subs`），时间按字数比例分配。
- 最终显示字幕**每条只显示一行**：中文上限 22 字（`subtitle_max_chars` /
  `VTV_CJK_LEN` / `cjk_len` / QC 的 `MAX_CJK_PER_LINE` 必须一致）。完整句过长时
  在标点或空格处拆成多条一行字幕；拉丁技术词（PagedAttention、vLLM、token）保持原子。
- `prep` 的 DeepSeek 审校把完整句合并成 **TTS 语义单元**（可跨多条显示字幕）；
  显示字幕在最终音画时间轴上按 TTS 单元重新分配，因此字幕与语音/画面同步。
  两者块数不同是正常的。
- 单语字幕（`hard`/`soft`）无论是否命中 TTS 语义单元，`_stage_align` 都会从最终
  时间轴按一行宽度重新切条；双语字幕保持源/目标 1:1 配对，不做二次切分。
- 非中文目标：一行宽度由 pyVideoTrans 的 `other_len`（默认 60）决定；
  `subtitle_max_chars` 目前只作用于 CJK，不要直接套用中文的 22 字。
- 硬字幕（默认）需要一次 NVENC 重编码；软字幕用 `--subtitle soft`，视频流可复制。
  硬/软二选一，不要把同名 SRT 放在视频旁。

## 翻译

- 术语表：`assets/glossary.txt`，格式 `原文=译文`；`setup`/`pipeline` 会注入。
- 语言 prompt：`vendor/pyvideotrans/videotrans/prompts/language_prompts/<lang>.txt`。
- SRT prompt 已要求 1:1 块保持、按每秒 3–4 字压缩、禁止跨块搬词。
- `aitrans_context=true` 注入全片上下文提升术语一致性；超长视频会增加 token。
- 翻译缓存键包含文本/渠道/模型；本项目已补上 glossary/prompt/context 哈希（见补丁）。改术语表后旧缓存仍可能命中，必要时清 `tmp/translate_cache`。
- `polish_srt.py` 可做第二轮文本审校：只改文本，块数/时间轴不变；失败的批次自动保留原文。

## 克隆配音

- 逐句从原片对应时间段裁参考音频（人声分离开启时从 `vocal.wav` 裁）。
- IndexTTS2 beam=3；实测 beam=1 只快约 10–20%，不建议为速度牺牲韵律。
- 默认 **audio_native**：`voice_autorate=false`、`max_audio_speed_rate=1.0`、
  `max_audio_slow_rate=1.0`，TTS 完全原速，不再为字幕槽拉伸/加速音频。
  音频长于槽位时由 `video_autorate` 慢放视频片段；音频短于槽位时把视频最多加快
  `max_video_speedup_rate`（1.25×）到“音频 + `max_phrase_pause_ms`（300ms）”。
- TTS 边缘统一处理：精确裁到首/末非静音，20ms fade in/out，前后各加 80ms 纯数字
  静音预滚；用于消除句间“嗖”声，也避免 TTS 自带边缘静音在句间叠加。
- `fit_merge` / `fit_condense` 只在**没有 TTS 语义单元**时兜底（DeepSeek 不可用或
  `resegment_llm_check=false`）：合并同说话人相邻短句，再压缩仍超速的句子。
  完整机制、调参、预测模型标定见 `audio-fit.md`。
- 单句参考太短/含噪声会不稳定；有 BGM 时建议预分离人声。
- 默认 `clone_ref_mode=line`：逐句参考，保留原句的情绪起伏；`fit_merge` 额外用
  CAMPPlus 聚类做“同说话人”合并键，只合并同一说话人的相邻短句，不会跨说话人。
  `cluster` 每人一个参考、音色最稳但情感较平；`single` 最快。
- 默认质量预设：beam 3、扩散 25 步；比 beam1/15 快配置慢约 17%。详见 `audio-fit.md`。

## 响度与音频

- 成品统一 `loudnorm=I=-18:TP=-1.5:LRA=11`，`-ar 48000 -c:a aac -b:a 192k`。
- 旧批次若出现 96 kHz，是 loudnorm 未指定 `-ar` 所致；重新归一化即可。
- 背景音：默认只保留配音；需要 BGM 时开启人声分离并重新嵌入 instrument。

## QC 门禁

| 指标 | 阈值 |
|---|---|
| 时长 | 在 `[源/1.25−2s, 源×1.5+2s]` 内（由 `max_video_speedup_rate` / `max_video_pts_rate` 决定），且不突破 `duration_tol_s` 的绝对下限（默认 15s） |
| 视频 | h264 / 1280x720 / yuv420p（可按源调整） |
| 音频 | AAC / 48 kHz / stereo |
| 响度 | -18 ±1 LUFS，true peak ≤ -1 dBFS |
| 字幕 | 每条 **1 行**、CJK ≤22 字，无空块/重复 |
| 句间停顿 | 约 `max_phrase_pause_ms`（默认 300ms），无异常长空白/爆音 |
| 术语命中 | ≥95%（允许保留英文术语） |
| ASR 回读 | 抽检 CER ≤10% |

## 后端选择

默认 IndexTTS2（质量优先，beam 3 / 扩散 25）；`qwen3local` / `f5` 是可选后端，
本机实测更慢，换后端前先按 `audio-fit.md` 重新标定并试听。
