# 流水线架构

## 阶段

```text
源视频
  │
  ├─ prep: ASR (faster-whisper large-v3-turbo) → en.srt
  │        MT  (DeepSeek + glossary + context) → zh-cn.srt（超长块拆分）
  │
  ├─ dub: vtv --no-clear-cache（预置 en.srt / zh-cn.srt，跳过 ASR/MT）
  │        prepare: 提取视频/音频、可选人声分离
  │        dubbing: 逐句裁剪参考音频 → IndexTTS2 克隆
  │        align: Rubberband 保音色变速 + 字幕重定时
  │        assembling: 硬/软字幕 + 音轨合成
  │
  └─ post: loudnorm -18 LUFS / 48 kHz → QC → 原子替换
```

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
