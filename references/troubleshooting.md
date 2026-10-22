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

先按下面“GPU 抢走 CPU 功耗”一节检查 GPU 时钟/CPU 性能；本机根因是 GPU 高频挤占 CPU，
锁 GPU 1200MHz 后 100 句无退化，`index_restart_every=0` 即可。

只有在 GPU 已锁频、CPU 性能稳定，但 GPT 仍随句数上升时，才考虑 daemon 退化：
`gradio_shim.py`/直连路径的 `INDEXTTS2_RESTART_EVERY=25` 每 25 句优雅重启，
下次请求冷启动约 20–30 秒；用 `scripts/tts_stability.py` 测最佳间隔。

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

## 电池供电时 TTS 慢 2–3 倍

现象：同一视频在电池下 20–30 秒/句，接电源后 5–10 秒/句。
原因：当前电源计划 DC 最小处理器状态 5%，GPU 也会降频。
处理：接 AC；`run`/`dub` 默认检测到电池会拒绝，确实要跑时加 `--allow-battery`。

## TTS 后段变慢（GPU 抢走 CPU 功耗）

现象：前 10 句 GPT ~5s，随后升到 15–19s；CPU `% Processor Performance` 从 ~175% 掉到 **22%**，
而 GPU 仍在 2700MHz、温度只有 65°C、没有 thermal throttle。原因是笔记本 CPU/GPU 共享功耗预算，
GPU 高频把小核/CPU 的频率挤没了，而 GPT 自回归占 TTS 墙钟 86%。

本机实测（RTX 5070 Ti Laptop + i7-14650HX）：

| 状态 | GPU 时钟 | CPU 性能 | GPT 中位 | 总耗时 |
|---|---:|---:|---:|---:|
| 不锁频、前 10 句 | 2737MHz | 174% | 3.5–5s | ~5.3s/句 |
| 不锁频、后段 | 2700MHz | 22% | 15–19s | 18–22s/句 |
| 锁 1200MHz、100 句 | 1177MHz | 179% | 3.83s | **5.9s/句** |

处理：

```bat
scripts\gpu_lock_task.cmd          :: 管理员运行，注册登录任务
scripts\gpu_lock_task.cmd remove   :: 撤销并 nvidia-smi -rgc
```

锁频期间游戏/3D 渲染性能受限；不跑 TTS 时可 `nvidia-smi -rgc` 恢复。
如果锁 1200MHz 后 GPU 出现持续 `SW Power Cap`/`HW Thermal`，再降低上限或改善散热；
不要盲目锁 3090。`scripts/tts_stability.py` 会同时记录 GPU 时钟、功耗、温度和 throttle。

## 重跑很快但 TTS 阶段只有几十秒（命中缓存）

现象：重渲染同一个视频，`[语音合成阶段结束耗时]` 只有几十秒，232 句“全部成功”。
原因：`DUBBING_CACHE` 是全局内容寻址缓存，文本/参考音频/参数相同就直接复制旧 wav，不会真正推理。
如果要验证 TTS 速度或强制重新合成，把缓存指向空目录：

```bash
VTV_DUBBING_CACHE=D:/tmp/dubbing_cache_fresh python scripts/pipeline.py run ...
```

`--force` 只重跑阶段，**不会**清逐句配音缓存。

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

## 换课程后 QC 术语命中率不对

翻译 prompt 用的是 `vendor/pyvideotrans/videotrans/glossary.txt`，而 `post`/`qc` 用的是
`config.json` 的 `glossary`。如果配置还指向上一个课程（例如 `glossary-vllm.txt`），
本课程的成品会被按旧术语表检查，出现“译文没问题但 QC 命中率低”。
跑不同课程时显式加 `--glossary assets/glossary.txt`（或对应课程术语表）。
术语有多个可接受译法时写 `原文=译法1|译法2`，例如 `chain=链|一连串`。

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

## TTS 跑 1–2 分钟后慢 3–4 倍（CPU 降频）

现象：IndexTTS2 前几句 5–8 秒，随后 GPT、s2mel、BigVGAN 同时慢 3–4 倍；重启 daemon 只能短暂恢复。

原因：Windows 电源计划为“平衡”时，CPU 被压在低 P-state（约 70% / 1.6GHz）。GPT 自回归是 CPU 驱动的小步循环，CPU 一降频，整条链路都慢。

检查：

```bash
powershell -NoProfile -Command "(Get-Counter '\Processor Information(_Total)\% Processor Performance').CounterSamples.CookedValue"
```

正常睿频约 150–250%；只有 70% 左右就是降频。

修复（无需重启 daemon，下一句即可提速）：

```bash
powercfg.exe -setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
```

Git Bash 里必须用 `-` 参数（`/setactive` 会被 MSYS 当成路径转换）。电池模式下高性能会更耗电，可按需切回“平衡”。

## TTS 慢 3–4 倍（线程落到 E-core / 提权未生效）

如果 CPU 频率看起来正常，但 GPT 仍然 18–30 秒/句，检查真实 daemon 的优先级和 CPU 亲和性：

```bash
PID=$(curl -s http://127.0.0.1:8791/health | python -c "import sys,json; print((json.load(sys.stdin).get('daemon') or {}).get('pid') or '')")
powershell -NoProfile -Command "Get-Process -Id $PID | Select-Object Id,PriorityClass,ProcessorAffinity"
```

正常应为 `PriorityClass=High`、`ProcessorAffinity=65535`（P-core 0–15）。
如果是 `Normal` 或全核（16777215），说明：

- `index_high_priority` 提的是 uv launcher，不是真正跑 GPT 的 daemon；
- `restart_before_tts` 之后新 daemon 没有重新提权；
- Windows 可能把单线程解码调度到 E-core（逻辑 CPU 16–23），RTF 从 ~2 掉到 ~7。

`config.json` 设 `pin_daemon=true` 后，直连路径会在每次 daemon 重启后按 `/health` 的真 PID 设置 High + P-core 亲和性。也可以手动修当前进程：

```bash
powershell -NoProfile -Command "(Get-Process -Id $PID).PriorityClass='High'; (Get-Process -Id $PID).ProcessorAffinity=[IntPtr]0xFFFF"
```

## 更多

pyVideoTrans 包装脚本的问题见 `references/troubleshooting-pyvideotrans.md`。
