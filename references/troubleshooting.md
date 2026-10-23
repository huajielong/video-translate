# 常见问题

## 执行任务时弹出 cmd / PowerShell 黑窗口

**原因**：Windows 给每个控制台子系统子进程（`python.exe`、`uv.exe`、`ffmpeg.exe`、
`cmd.exe` 跑 `.cmd`）分配新控制台窗口——只要父进程自己没有控制台。从编辑器/Agent
驱动技能时就是这种情况；重定向 stdout/stderr 并不能阻止窗口。

两个最容易漏掉的中间层：

- `venv\Scripts\pythonw.exe` 是 uv launcher，它会再启动
  `uv\python\...\python.exe`；**那个子进程**创建 `conhost`，就是空标题一闪而过的黑窗。
- `indextts2.cmd` 内部会启动 `powershell` / `where`，孙进程同样可能闪窗。

典型触发点：`serve` 的 API/shim、daemon 启动、每片 TTS 前的 daemon stop、API 里的
ffmpeg 转码。

**处理（本版本已改）**：
- 入口用 `pythonw scripts/pipeline.pyw <command>`：解释器没有控制台，不会在启动时
  分配窗口；纯 pythonw 下 `print` 输出到 `.runtime/logs/entry.log`。
- API/shim/daemon 改用 **base `pythonw`**（`pyvenv.cfg` 的 `home`），venv 的
  `site-packages` 通过 `PYTHONPATH`（`VTV_ITT_SITE`）传入；不再经过 venv launcher。
- daemon stop 直接调用 `pythonw + scripts/indextts2.py stop --json`，不再经过
  `indextts2.cmd`；`scripts/gpu_lock_task.pyw` 替代 `.cmd` 注册脚本。
- `scripts/_winhide.py` 在入口进程内把 `subprocess.Popen` 默认加上
  `CREATE_NO_WINDOW`；`_winhide_site/sitecustomize.py` 通过 `PYTHONPATH` 覆盖
  Python 孙进程（含 API 内的 ffmpeg）。刻意使用 `DETACHED_PROCESS` /
  `CREATE_NEW_CONSOLE` 的调用保持原样。
- 验证：`serve` 后 API/shim/daemon 三条命令都应是 `uv\python\...\pythonw.exe`，
  且由它们创建的 `conhost.exe` 为 0。若仍有窗口，说明是修复前的旧进程：
  `pipeline.py serve --stop` 后重新 `serve`。
- 完整进程模型见 `deployment.md`。

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

## 字幕被切成两段 / 断句不完整

- `prep` 自动跑 `resegment_srt.py`：源 74 → 45 左右，目标显示字幕按 **一行 22 字** 切；
- `resegment_llm_check=true` 时，DeepSeek 会审校整段并生成 `<target>.tts.srt`
  （001 实测 80 条一行显示字幕 → 31 个 TTS 语义单元）。日志出现
  `DeepSeek TTS groups -> ...` 才表示审校生效；
- 若日志显示 `DeepSeek unavailable` 或 `invalid group list`，会回退规则断句；
  检查 DeepSeek Key/网络，或临时设 `resegment_llm_check=false`；
- 显示字幕仍按短块烧录，TTS 使用合并后的完整句；两者块数不同是正常的。

## 字幕与音画不同步

现象：配音和画面同步，但字幕整体提前/滞后，或某段后越差越多。
原因：视频片段被 `video_autorate` 变速后，若没有把 `queue_tts` 更新到最终视频
时间轴，字幕仍按源时间写入。

处理：本项目 `_stage_align.py` 已从最终 queue 时间轴重新生成显示字幕；确认：

- 日志有 `[Video-Cut]` 与 `视频慢速处理完毕`，且成品时长和 queue 总时长一致；
- `_字幕/<stem>.zh-cn.srt` 是本次新生成的（mtime 晚于成品），不是旧文件；
- 使用 `VTV_TTS_SRT` 时，显示字幕走 `_display_items`，不要用 TTS 单元直接覆盖。

若仍错位，先跑 `qc` 看块数和时间，再重跑 `dub`（不要只重跑 `post`）。

## 片头被截掉

`video_autorate` 的片段队列从首条字幕起点开始；如果首条字幕不是 0，片头画面会被
裁掉。处理：把首条字幕起点改为 0（或补一条 0 点字幕），或关闭 `video_autorate`。
片尾由最后一段保留到视频结束，正常不会丢。

## QC 报时长超出视频变速边界

`video_autorate` 会让成品总时长随配音长度变化。QC 先按配置计算理论边界：

```text
下界 ≈ 源时长 / max_video_speedup_rate - 2s   # 1.25× -> 不低于源 80%
上界 ≈ 源时长 × max_video_pts_rate + 2s        # 1.5×  -> 不高于源 150%
```

`duration_tol_s`（默认 15s）只是绝对下限，不会把上下界合并成一个宽松值。处理：

- 低于下界：检查是否有 TTS 失败行/静音占位，或视频被异常过度加快；
- 高于上界：配音过长、重复，或视频慢放过度；
- 需要更严格时调小 `max_video_speedup_rate`，但会重新留下更多句间空白；
- 不要为了过 QC 把 `max_video_speedup_rate` 降到 1.0（会回到长空白模式）。

## 句间静音太长 / 一段话和下一段间隔太久

现象：配音本身正常，但每句之间停顿很长，听起来卡顿、不连贯；音画时间轴没变。
原因：对齐阶段把每条字幕的区间设为“本句开始到下一句开始”，若 TTS 音频短于该区间，
旧逻辑用数字静音补满，TTS 越短空档越大。002 的 232 句里曾累计约 289 秒静音。
处理（本版本默认 audio_native）：
- `voice_autorate=false`、`max_audio_slow_rate=1.0`：TTS 保持原速，音频短于槽位时
  就保留自然静音，不再为了填满而拉慢；
- 音频长于槽位时由 `video_autorate` 慢放视频片段吸收；只有关闭 `video_autorate`
  时才退回音频填缝（空档 ≤`dubb_natural_pause_ms` 默认 600ms 保持原速，超过才最多
  放慢 `max_audio_slow_rate`）；
- `dubb_min_pause_ms`（默认 200ms）是音频填缝的底线；
- 对齐前把每个 TTS 单元精确裁到首/末非静音，20ms fade in/out，前后各加 **80ms
  纯数字静音**；音频短于槽位时把视频最多加快 `max_video_speedup_rate`（默认 1.25）
  到“音频 + `max_phrase_pause_ms`（默认 300ms）”，末段保留到片尾，所以不会再把整段
  源空白留成 1–2s 死气；如果仍觉得某处停顿长，先看 `silencedetect` 是否真的 >1s；
- 想保留更多原片停顿：调小 `max_video_speedup_rate` 或调大 `max_phrase_pause_ms`；
- 想切碎/更紧凑：调小 `resegment_max_gap_ms` 或关闭 `resegment_llm_check`；
- 修改后重跑同一命令即可，逐句 TTS 缓存会复用。

## 句间有“嗖”声/爆音

现象：两句之间的空白里出现短促的高频噪声，像呼吸声/摩擦声；语音本身正常。
原因：TTS 输出首尾常带低电平气声或边缘噪声；旧逻辑保留约 120ms 边缘并直接拼接，
全片 loudnorm 后会被抬成可听见的“嗖”。画面缩放/裁剪不会影响音轨，不是视频的问题。

处理（本版本默认）：

- `_stage_align._trim_tts_silence` 精确裁到首/末非静音采样，20ms fade in/out，
  前后各加 **80ms 纯数字静音**；先确认成品是重跑后的新文件，旧成品不会自动更新。
- 如果仍有：先判断声音在“开口的第一个字”还是“纯空白”。在开口处说明是 TTS 对该
  字/缩写的发音（例如“KV”），可把配音文本改成中文说法（“键值缓存”），字幕保留原词；
  在纯空白处则把 fade 加到 40–50ms、预滚加到 120ms 再试。
- 用 `ffmpeg -af silencedetect` 或 Audacity 看波形确认位置；不要靠调视频 `-vf`
  缩放/裁剪解决，它不会影响音轨。

## 音频忽快忽慢 / 短句被加速 2–3 倍

现象：长句被加速到 2× 以上，短句又被放慢，整段听感忽快忽慢；常见于 5–10 个汉字却只占
0.7–1.5 秒槽位的碎片句。002 旧版 232 句里 21 句需要 >1.5×，最大 2.75×。

原因：IndexTTS2 每句有约 **1.3 秒固定开销**，5 个汉字也要约 1.6 秒音频；槽位不是按
“这句话需要多久”给的，而是按原字幕的下一句开始时间给的。单纯压缩文字救不了极短槽位。

audio_native 下如果仍看到音频变速：

- 先看对齐日志 `开始语音视频字幕对齐处理：音频加速=False, 视频慢速=True`；
  如果 `音频加速=True`，说明仍传了 `--voice_autorate`，检查 `config.json`；
- `_rate.py` 旧逻辑把 `audio_target` 默认设为整个槽位，导致空档 ≤natural 也被
  Rubberband 拉慢（日志倍率 0.83）；本版本已改为默认 `dubb_duration`（原速）。

处理（fit 兜底，无 TTS 语义单元时本版本默认开启）：

- `fit_merge`：在说话人聚类之后，把同一说话人、间隔 ≤`fit_gap_ms`（默认 500ms）的
  相邻短句合并成一个配音单元，共享固定开销；fit 兜底时每条最多 `fit_max_chars`
  （默认 44）个汉字。默认 audio_native 用 DeepSeek TTS 语义单元，显示字幕每条
  一行（22 字），过长拆条；若前一句没有句末标点（被 ASR/翻译切断），间隔 ≤900ms 时
  即使不超速也会合并，避免句中断裂。
- `fit_condense`：合并后实际/预测仍超过 `fit_condense_speed`（默认 1.25×）的句子，用
  DeepSeek 压缩到 `fit_max_speed`（默认 1.12×）对应的字数；术语表里的词不会被删。
- fit 兜底历史实测（002）：232 句合并为 199 句，最大加速 **2.75× → 1.44×**，
  >1.5× 的句子 **21 → 0**，中位 0.77×、P90 1.05×；QC 仍为 PASS（199 条字幕、
  每条一行、时长差 0.06s）。
- 当前默认 **audio_native**（TTS 原速 + 视频局部变速 + 300ms 停顿）：001 trial23
  成品 204.2s、80 条一行字幕、QC PASS；日志里不应再出现 0.83× 的音频拉慢。

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

日志提示 `Rubberband 不可用，将使用 pydub+ffmpeg`。仓库不带 Rubberband 二进制；
Windows 请把 `rubberband.exe` 放到 PATH 或 `tools/rubberband/`，Linux
`apt install rubberband-cli`，macOS `brew install rubberband`。`pipeline.py` 会把
`tools/rubberband/` 加入 PATH。

## DeepSeek Key 验证

不要用 `GET /models` 验 Key（会假阴性）；用 `trans_cli.py test-channel --channel deepseek` 或直接跑一条翻译。

## 断点续跑

`.runtime/state/<stem>.json` + 逐行 `DUBBING_CACHE`；重跑同一命令会跳过已完成阶段。当前版本每行成功后会写缓存（补丁），崩溃损失最小。

## TTS 变慢（CPU 降频 / E-core / 提权）

见 `performance.md`：电源计划、CPU 性能计数器、daemon High + P-core 自绑定。

## 更多

pyVideoTrans 包装脚本的问题见 `references/troubleshooting-pyvideotrans.md`。
