# 质量策略

## 字幕

- 翻译后把超长块按标点拆成 ≤28 字（`_split_long_subs`），时间按字数比例分配。
- 显示时按 15 字/行平衡断成两行；标点优先，拉丁词（PagedAttention、vLLM、token）保持原子。
- 双语/软字幕：`--subtitle soft`（`--subtitle-type 2`）可让视频流无损复制；不要把同名 SRT 放在视频旁。
- 硬字幕（默认）需要一次 NVENC 重编码；软字幕适合可开关字幕的播放器。

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
- `--voice_autorate`：配音超过字幕槽时加速；Rubberband 保音色。`max_audio_speed_rate=2.5` 是兜底；
  正常应靠 `fit_merge`/`fit_condense` 把“配音/槽位”压到 1.1× 左右，而不是靠大倍率加速。
  完整机制、调参、预测模型标定见 `audio-fit.md`。
- 单句参考太短/含噪声会不稳定；有 BGM 时建议预分离人声。
- 默认 `clone_ref_mode=line`：逐句参考，保留原句的情绪起伏；`fit_merge` 额外用
  CAMPPlus 聚类做“同说话人”合并键，只合并同一说话人的相邻短句，不会跨说话人。
  `cluster` 每人一个参考、音色最稳但情感较平；`single` 最快。
- 默认质量预设：beam 3、扩散 25 步、放慢上限 1.05、自然停顿 600ms；
  比 beam1/15 快配置慢约 17%。详见 `audio-fit.md`。

## 响度与音频

- 成品统一 `loudnorm=I=-18:TP=-1.5:LRA=11`，`-ar 48000 -c:a aac -b:a 192k`。
- 旧批次若出现 96 kHz，是 loudnorm 未指定 `-ar` 所致；重新归一化即可。
- 背景音：默认只保留配音；需要 BGM 时开启人声分离并重新嵌入 instrument。

## QC 门禁

| 指标 | 阈值 |
|---|---|
| 时长差 | ≤1 s |
| 视频 | h264 / 1280x720 / yuv420p（可按源调整） |
| 音频 | AAC / 48 kHz / stereo |
| 响度 | -18 ±1 LUFS，true peak ≤ -1 dBFS |
| 字幕 | 每块 ≤2 行，无空块/重复 |
| 术语命中 | ≥95%（允许保留英文术语） |
| ASR 回读 | 抽检 CER ≤10% |

## A/B 结论（本机 RTX 5070 Ti Laptop 12 GB）

| 后端 | RTF | 显存 | 结论 |
|---|---:|---:|---|
| IndexTTS2 (beam 3) | 2.5–3.4（fresh） | ~7.6 GB | **生产默认** |
| IndexTTS2 (beam 1) | ~10–20% 更快 | ~7.6 GB | 质量略降 |
| Qwen3-TTS 0.6B (eager) | 4.7–4.9 | ~3.4 GB | 不达速度门禁 |
| Qwen3-TTS 0.6B (SDPA+bf16) | 3.7 | ~3.4 GB | 仍慢于 Index |

Qwen 的瓶颈是缺少 flash-attn 与 sm_120 上的手动 attention；换有 flash-attn 的环境后可重测。
