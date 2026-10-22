# 常见问题

## 执行任务时弹出 cmd / PowerShell 黑窗口

**原因**：Windows 给每个控制台子系统子进程（`python.exe`、`uv.exe`、`ffmpeg.exe`、
`cmd.exe` 跑 `.cmd`）分配新控制台窗口——只要父进程自己没有控制台。从编辑器/Agent
驱动技能时就是这种情况；重定向 stdout/stderr 并不能阻止窗口。典型来源是 `serve`
的 IndexTTS2 API、Gradio 桥接、每片 TTS 前的 `indextts2.cmd stop`、以及 API 内的
ffmpeg 转码。

**处理（本版本已改）**：
- `scripts/_winhide.py` 在入口进程内把 `subprocess.Popen` 默认加上
  `CREATE_NO_WINDOW`；`pipeline.py`、`trans_cli.py`、`gradio_shim.py`、`qc.py`、
  `tts_stability.py` 均调用。
- `scripts/_winhide_site/sitecustomize.py` 通过 `PYTHONPATH` 注入 IndexTTS2 API
  及其 Python 子进程，覆盖 API 内部的 ffmpeg 调用。
- 刻意使用 `DETACHED_PROCESS` / `CREATE_NEW_CONSOLE` 的调用保持原样（Windows
  不允许与 `CREATE_NO_WINDOW` 组合）。
- 要连“一瞬间”都没有：用 `pythonw scripts/pipeline.pyw <command>` 启动。`pythonw`
  没有控制台，Windows 不会在解释器启动时分配窗口；纯 pythonw 下 `print` 输出到
  `.runtime/logs/entry.log`。
- 若仍有窗口，说明是技能之外的旧进程：`pipeline.py serve --stop` 后重新 `serve`；
  检查是否有旧版 `gradio_shim.py` / `indextts2` 仍在运行。

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

先读 `performance.md`：GPU 锁频、CPU 性能、daemon 优先级/亲和性、probe/tts_stability。
GPU 已锁频且 CPU 稳定但 GPT 仍随句数上升时，才考虑 `index_restart_every`。

## 句间静音太长 / 一段话和下一段间隔太久

现象：配音本身正常，但每句之间停顿很长，听起来卡顿、不连贯；音画时间轴没变。
原因：对齐阶段把每条字幕的区间设为“本句开始到下一句开始”，若 TTS 音频短于该区间，
旧逻辑用数字静音补满，TTS 越短空档越大。002 的 232 句里曾累计约 289 秒静音。
处理（本版本已改）：
- 空档 ≤`dubb_natural_pause_ms`（默认 600ms）时保持原速；只有超过它才允许最多
  放慢 `max_audio_slow_rate`（默认 1.05）去填，填到 natural 附近；
- `dubb_min_pause_ms`（默认 200ms）是底线；
- 第一句不再把开头 4–5 秒算进自己的区间；
- 这是 quality_merge 默认：优先自然语速，避免每条短句都被拉慢；
- 想更紧凑：把 `dubb_natural_pause_ms` 降到 400–500、`max_audio_slow_rate` 调到 1.1；
- 想更自然：把 `dubb_natural_pause_ms` 调到 800、`max_audio_slow_rate` 调到 1.0；
- 修改后重跑同一命令即可，逐句 TTS 缓存会复用。

## 音频忽快忽慢 / 短句被加速 2–3 倍

现象：长句被加速到 2× 以上，短句又被放慢，整段听感忽快忽慢；常见于 5–10 个汉字却只占
0.7–1.5 秒槽位的碎片句。002 旧版 232 句里 21 句需要 >1.5×，最大 2.75×。

原因：IndexTTS2 每句有约 **1.3 秒固定开销**，5 个汉字也要约 1.6 秒音频；槽位不是按
“这句话需要多久”给的，而是按原字幕的下一句开始时间给的。单纯压缩文字救不了极短槽位。

处理（本版本默认开启）：

- `fit_merge`：在说话人聚类之后，把同一说话人、间隔 ≤`fit_gap_ms`（默认 500ms）的
  相邻短句合并成一个配音单元，共享固定开销；合并后每条最多 `fit_max_chars`（默认 36）
  个汉字，仍保持两行显示。
- `fit_condense`：合并后实际/预测仍超过 `fit_condense_speed`（默认 1.25×）的句子，用
  DeepSeek 压缩到 `fit_max_speed`（默认 1.12×）对应的字数；术语表里的词不会被删。
- 002 实测：232 句合并为 199 句，最大加速 **2.75× → 1.44×**，>1.5× 的句子 **21 → 0**，
  中位 0.77×、P90 1.05×；QC 仍为 PASS（199 条字幕、每条 ≤2 行、时长差 0.06s）。
- 默认 **quality_merge**（`line` 逐句参考 + beam3/25 + 放慢 1.05/停顿 600）：
  vLLM 001 实际语速 **0.95–1.23×、中位 1.00×**，>1.5× 0 句，最长静音 1.47s。

字幕条数会变少（002 是 199 条），这是合并短句的正常结果；如果要保留逐句字幕，
设 `fit_merge=false`，但短句仍会被加速。完整机制、边界和排错见 `audio-fit.md`；
换 TTS 后端或音色后用 `python scripts/fit_calibrate.py --pid <daemon_pid>` 重新标定
`fit_pred_a`/`fit_pred_b`。

## 电池 / GPU 抢功耗 / CPU 降频

见 `performance.md`：AC 预检、GPU 锁频、CPU 性能计数器、daemon 自绑定、基准工具。

## 重跑很快但 TTS 阶段只有几十秒（命中缓存）

现象：重渲染同一个视频，`[语音合成阶段结束耗时]` 只有几十秒，232 句“全部成功”。
原因：`DUBBING_CACHE` 是全局内容寻址缓存，文本/参考音频/参数相同就直接复制旧 wav，不会真正推理。
如果要验证 TTS 速度或强制重新合成，把缓存指向空目录：

```bash
VTV_DUBBING_CACHE=D:/tmp/dubbing_cache_fresh python scripts/pipeline.py run ...
```

`--force` 只重跑阶段，**不会**清逐句配音缓存。

## 预置 SRT 被删 / `xxx is not exists`

上游 `clear_cache` 默认开启，会递归删除 `--outdir`。dub-only 必须同时传 `--overwrite --no-clear-cache`。本项目 `pipeline.py` 已自动处理。

## 改了术语表但翻译没变

翻译缓存键不含 glossary/prompt 内容（旧版）；改完术语表后清 `vendor/pyvideotrans/tmp/translate_cache`，或换一个输出/工作目录。

## 换课程后 QC 术语命中率不对

翻译 prompt 用的是 `vendor/pyvideotrans/videotrans/glossary.txt`，而 `post`/`qc` 用的是
`config.json` 的 `glossary`。如果配置还指向上一个课程的术语表，
本课程的成品会被按旧术语表检查，出现“译文没问题但 QC 命中率低”。
跑不同课程时显式加 `--glossary assets/glossary.txt`（或对应课程术语表）。
术语有多个可接受译法时写 `原文=译法1|译法2`，例如 `chain=链|一连串`。

## 改了音色/参考音频但配音没变

`DUBBING_CACHE` 键不含参考音频/模型名（旧版）；换后端或参考时清 `vendor/pyvideotrans/tmp/dubbing_cache`，或换缓存命名空间。

## Rubberband 不可用

日志提示 `Rubberband 不可用，将使用 pydub+ffmpeg`。Windows 包内已带 `tools/rubberband/.../rubberband.exe`；Linux `apt install rubberband-cli`；macOS `brew install rubberband`。`pipeline.py` 会把该目录加入 PATH。

## DeepSeek Key 验证

不要用 `GET /models` 验 Key（会假阴性）；用 `trans_cli.py test-channel --channel deepseek` 或直接跑一条翻译。

## 断点续跑

`.runtime/state/<stem>.json` + 逐行 `DUBBING_CACHE`；重跑同一命令会跳过已完成阶段。当前版本每行成功后会写缓存（补丁），崩溃损失最小。

## TTS 变慢（CPU 降频 / E-core / 提权）

见 `performance.md`：电源计划、CPU 性能计数器、daemon High + P-core 自绑定。

## 更多

pyVideoTrans 包装脚本的问题见 `references/troubleshooting-pyvideotrans.md`。
