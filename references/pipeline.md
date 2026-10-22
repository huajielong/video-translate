# 流水线架构

## 阶段

```text
源视频
  │
  ├─ prep: ASR (faster-whisper large-v3-turbo) 或旁路字幕（.en-us.vtt/.en.vtt/.en.srt）→ en.srt
  │        MT  (DeepSeek + glossary + context) → zh-cn.srt（超长块拆分）
  │
  ├─ dub: vtv --no-clear-cache（预置 en.srt / zh-cn.srt，跳过 ASR/MT）
  │        prepare: 提取视频/音频、可选人声分离
  │        refs: 逐句参考 + CAMPPlus 聚类（line 保留逐句 ref，聚类只做合并键）
  │        fit: 合并同说话人短句 + 压缩仍超速句（TTS 前，只改文本/时间）
  │        tts: IndexTTS2 克隆（默认 beam 3 / 扩散 25）
  │        align: Rubberband 保音色变速 + 字幕重定时
  │        assembling: 硬/软字幕 + 音轨合成
  │
  └─ post: 两遍 loudnorm -18 LUFS / 48 kHz → 按源时长裁剪 → QC → 原子替换
```

## 配音单元拟合（fit）

`dub` 内部、说话人聚类之后、TTS 之前：

- `fit_merge`：同一说话人（`cluster`/`single` 用参考路径，`line` 用 CAMPPlus
  聚类键）、间隔 ≤500ms 的相邻短句，合并后 ≤36 字且仍能两行显示，就合并成
  一个配音单元；优先合并最超速的句子。
- `fit_condense`：合并后仍 >1.25× 的句子，用 DeepSeek 压到约 1.12×；
  术语/英文技术词丢失则拒绝。重跑时优先用 `DUBBING_CACHE` 的真实音频时长判断。
- 合并/压缩只改目标文本与字幕条数，不改视频、不切视频；`video_autorate` 仍关闭。
- 详细原理、配置、标定与排错见 `audio-fit.md`。

## 目录

```text
<out>/
  <stem>.mp4                 成品
  _字幕/
    <stem>_中文.srt
    <stem>_原文.srt
  _work/                     中间文件（可删）
    prep/<stem>/{stt,mt,en.srt,zh-cn.srt}
    <stem>/{en.srt,zh-cn.srt,<stem>.mp4}
  QC_SUMMARY.md
```

`<skill>/.runtime/logs/` 放每片的 prep/dub/post 日志与 `*.qc.json`；`<skill>/.runtime/state/` 放断点状态。

## 并发策略

| 阶段 | 并发 | 说明 |
|---|---|---|
| ASR | 1 | large-v3-turbo 约 4 GB 显存 |
| 翻译 | `mt_workers`（默认 4） | 纯网络等待；DeepSeek 限流时调低 |
| IndexTTS2 配音 | 1 | 守护进程单 worker；多开只会排队 |
| Qwen3-TTS 配音 | 1–2 | 每实例 3–4 GB；本机实测太慢，默认不用 |
| 后处理/QC | 可与下一片配音重叠 | ffmpeg/CPU |

**不要**让 ASR 与 TTS 同时占用 GPU；12 GB 卡上容易 OOM。

本机（RTX 5070 Ti Laptop）额外注意：GPU 高频会挤占 CPU 功耗，使 GPT 从 3.6s/句退化到 15–19s/句。
`scripts/gpu_lock_task.cmd` 注册登录任务，把 GPU 上限锁到 1200MHz 后，CPU 恢复、TTS 稳定在 5–6s/句；
详见 `configuration.md` 的“GPU 锁频任务”。

## 断点续跑

- `.runtime/state/<stem>.json` 记录 asr/mt/dub/post/qc。
- `prep` 只在 `en.srt` / `<target>.srt` 不存在时重跑；`--force` 可强制重跑 ASR/翻译。
- `dub` 复用 pyVideoTrans 的逐行 `DUBBING_CACHE`（键含文本/音色/参数）；`--force` 不清该缓存。
- 失败重跑同一命令即可；已成功的行不会重复合成。
- 批次里单条失败不会中断其他视频；最终退出码为 1，用 `status` 看逐片结果。

## dub-only 的关键参数

```bash
trans_cli.py vtv <src> --source en --target zh-cn \
  --tts-channel index --voice clone \
  --outdir <work> --overwrite --no-clear-cache \
  --extra-args=--voice_autorate
```

- 预置文件必须是 `<outdir>/en.srt` 与 `<outdir>/zh-cn.srt`。
- `--overwrite` 让包装脚本接受非空目录；`--no-clear-cache` 阻止上游删除目录。
- 只给 `--overwrite` 会丢掉预置 SRT。

## 配置与路径

配置键、路径解析、`params.json` 与环境变量统一见 `configuration.md`；命令与参数见 `cli.md`。
