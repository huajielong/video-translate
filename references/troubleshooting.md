# 常见问题

## 成品有两层字幕

**原因**：视频已烧录硬字幕，播放器又自动加载了同名的 `.srt`。
**处理**：把 SRT 放到 `_字幕/` 子目录；或在播放器里关闭外挂字幕。不要删除硬字幕层。
软字幕模式（`--subtitle soft`）把字幕内嵌进 MP4，不会触发外挂加载。

## IndexTTS2 CUDA_FATAL / 音轨变成几十分钟

现象：日志出现 `CUDA error: an illegal instruction was encountered`，成品音轨采样率异常（如 11025 Hz）、时长是源片的数倍。
原因：失败的配音行留下异常残片，旧版 `_rate.py` 用 `-c:a copy` 拼接，把残片格式当成输出格式。
处理：
- 本项目已把 concat 改为 `-ar 48000 -ac 2 -c:a pcm_s16le`；
- 失败行应从逐行缓存重建，或重跑 `dub`（`DUBBING_CACHE` 会复用 190 句，只补失败行）；
- `doctor`/QC 会检查成品采样率和时长。

## IndexTTS2 越跑越慢

现象：每句从 7–12 秒退化到 20–55 秒。
原因：单 worker 长跑后的 CUDA/显存/功耗状态累积。
处理：`gradio_shim.py` 默认 `INDEXTTS2_RESTART_EVERY=25`，每 25 句优雅重启守护进程；下次请求自动冷启动（约 24 秒）。可调大/调小，0 关闭。

## Qwen3-TTS 很慢

本机实测 RTF 3.7–4.9（无 flash-attn），远高于 IndexTTS2。若要用 Qwen：
- 安装与 torch/GPU 匹配的 flash-attn；
- 用 `attn_implementation="sdpa"`、`dtype="bfloat16"`；
- 每个视频只 `create_voice_clone_prompt` 一次并复用；
- 用有更强 GPU 的机器。
否则保持 `--backend index`。

## 预置 SRT 被删 / `xxx is not exists`

上游 `clear_cache` 默认开启，会递归删除 `--outdir`。dub-only 必须同时传 `--overwrite --no-clear-cache`。本项目 `pipeline.py` 已自动处理。

## 改了术语表但翻译没变

翻译缓存键不含 glossary/prompt 内容（旧版）；改完术语表后清 `vendor/pyvideotrans/tmp/translate_cache`，或换一个输出/工作目录。

## 改了音色/参考音频但配音没变

`DUBBING_CACHE` 键不含参考音频/模型名（旧版）；换后端或参考时清 `vendor/pyvideotrans/tmp/dubbing_cache`，或换缓存命名空间。

## Rubberband 不可用

日志提示 `Rubberband 不可用，将使用 pydub+ffmpeg`。Windows 包内已带 `tools/rubberband/.../rubberband.exe`；Linux `apt install rubberband-cli`；macOS `brew install rubberband`。`pipeline.py` 会把该目录加入 PATH。

## DeepSeek Key 验证

不要用 `GET /models` 验 Key（会假阴性）；用 `trans_cli.py test-channel --channel deepseek` 或直接跑一条翻译。

## 单句字幕 + Edge-TTS 无输出

上游已知问题：`tts` 任务只有一行字幕且 TTS 为 Edge 时不会产出。加一行或换 TTS 渠道。

## 断点续跑

`.runtime/state/<stem>.json` + 逐行 `DUBBING_CACHE`；重跑同一命令会跳过已完成阶段。当前版本每行成功后会写缓存（补丁），崩溃损失最小。

## 更多

pyVideoTrans 包装脚本的问题见 `references/troubleshooting-pyvideotrans.md`。
