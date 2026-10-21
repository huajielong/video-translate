# 配置与路径

## 优先级

`命令行参数` > `config.json` > `scripts/pipeline.py` 的 `DEFAULTS`。

`config.json` 由 `setup` 写入技能根目录；模板见 `config.example.json`。发到新机器前应删除它（含本机路径）。

## `config.json` 键

| 键 | 默认 | 说明 |
|---|---|---|
| `pyvideotrans_home` | `vendor/pyvideotrans` | pyVideoTrans 源码/venv 根目录；可指向外部安装 |
| `index_tts2_home` | `vendor/index-tts2-cli` | IndexTTS2 根目录；可指向外部安装 |
| `src_dir` | 空 | 源视频目录；可被 `--src` 覆盖 |
| `out_dir` | 空 | 成品目录；可被 `--out` 覆盖 |
| `work_dir` | `_work` | 中间目录；相对路径按 `out_dir` 解析 |
| `backend` | `index` | `index` / `qwen3local` / `f5` |
| `source_lang` | `en` | 源语言代码，见 `languages.md` |
| `target_lang` | `zh-cn` | 目标语言代码 |
| `subtitle_mode` | `hard` | `none` / `hard` / `soft` / `dual-hard` / `dual-soft` |
| `translate_channel` | `deepseek` | 翻译渠道名，见 `channels.md` |
| `deepseek_model` | `deepseek-flash` | DeepSeek 模型名 |
| `asr_model` | `large-v3-turbo` | Whisper 模型，见 `models.md` |
| `index_api` | `http://127.0.0.1:8791` | IndexTTS2 HTTP API |
| `index_url` | `http://127.0.0.1:7861` | Gradio 桥接地址（pyVideoTrans 的 `index` 渠道） |
| `index_api_port` | `8791` | `serve` 启动 API 时使用 |
| `shim_port` | `7861` | `serve` 启动桥接时使用 |
| `index_fast` | `false` | `true` 时 beam 3→1，快约 10–20%，韵律下降 |
| `index_restart_every` | `0` | 每 N 句重启守护进程；GPU 锁频下 100 句无退化，保持 0；若未锁 GPU/仍退化，可设 25 或更小 |
| `restart_before_tts` | `true` | prepare 后、TTS 前重启一次 daemon，避免 prepare 阶段把 GPU 带入慢状态 |
| `pin_daemon` | `true` | 把真实 daemon 固定到 P-core（0–15）并设 High；daemon 启动时自绑定，客户端还会兜底 |
| `probe` | `true` | daemon 把逐句耗时写入 `.runtime/logs/tts_probe.jsonl`；改完需重启 `serve` |
| `daemon_affinity_mask` | `0xFFFF` | daemon 自绑定使用的 CPU 掩码；24 线程 i7-14650HX 的 P-core 0–15 |
| `gpu_lock_clock` | `0` | 可选：非 0 时由**管理员权限的** `serve` 用 `nvidia-smi -lgc X,X` 锁 GPU 时钟；普通用户请用 `scripts/gpu_lock_task.cmd` 注册登录任务 |
| `clone_ref_mode` | `cluster` | `single`=整片固定参考（最快，单人）；`line`=逐句参考（多人但慢）；`cluster`=按说话人聚类，每人一个参考 |
| `speaker_max` | `0` | 聚类最大说话人数；`0` = 自动 |
| `speaker_threshold` | `0.72` | 聚类余弦相似度阈值；越高越容易分出更多说话人 |
| `speaker_merge_sim` | `0.80` | 小簇与更大簇的质心相似度达到该值才合并；**不按句数合并** |
| `speaker_self_consistency` | `0.55` | 单句簇前后半段相似度低于该值才视为混音/噪声 |
| `index_threads` | `0` | 仅给 daemon 设 `OMP_NUM_THREADS`；`0` = torch 默认。实测调大无收益，同时设 MKL/NumExpr 会严重反噬 |
| `index_high_priority` | `true` | `serve` 把 API/shim/daemon 提到 High 优先级 |
| `index_diffusion_steps` | `25` | s2mel 扩散步数；15 步约省 0.4–0.5s/句，10 步收益饱和，音质需试听 |
| `mt_workers` | `4` | 翻译并发（纯网络等待） |
| `voice_autorate` | `true` | 传 `--voice_autorate`，用音频变速对齐字幕 |
| `video_autorate` | `false` | 传 `--video_autorate`，用视频慢速分担；**当前上游实现会丢片头/片尾，修好前不要开** |
| `max_video_pts_rate` | `1.5` | 视频慢速封顶；配合 `video_autorate` |
| `max_audio_speed_rate` | `30` | 配音长于字幕区间时最大加速倍率；越小越自然，但可能破坏同步 |
| `max_audio_slow_rate` | `1.2` | 配音短于区间时允许的最大放慢倍率，用来填补句间空档；`1.0` 关闭 |
| `dubb_min_pause_ms` | `200` | 每句结束至少保留的停顿毫秒数；放慢填缝不会低于该停顿 |
| `fit_merge` | `true` | 合并同说话人、时间相邻的短句，减少 IndexTTS2 固定开销导致的 2–3× 加速 |
| `fit_condense` | `true` | 合并后仍超速的句子用 DeepSeek 压缩改写；需要 DeepSeek Key |
| `fit_force` | `false` | `fit_*` 只对 IndexTTS2 自动启用；其他后端标定后设 `true` 强制 |
| `fit_max_speed` | `1.12` | 目标最大“配音/槽位”时长比，合并与压缩共用 |
| `fit_max_chars` | `36` | 合并后单条字幕最多汉字数，约两行显示 |
| `fit_gap_ms` | `500` | 相邻句间隔超过该值不合并，保留原本的说话停顿 |
| `fit_pred_a` / `fit_pred_b` | `1.30` / `0.123` | IndexTTS2 时长预测 `秒 ≈ a + b×汉字数`；换后端/音色需重标定 |
| `fit_condense_speed` | `1.25` | 预测/实际时长比超过该值才触发压缩；避免过度改写 |
| `loudness` | `-18.0` | 成品集成响度（LUFS） |

> `fit_*` 默认值只对中文 + IndexTTS2 标定；换后端/音色/语言后用
> `python scripts/fit_calibrate.py --pid <daemon_pid>` 重新拟合，机制与边界见 `audio-fit.md`。
| `final_suffix` | 空 | 成品文件名后缀，如 `_中文`；状态检查与输出路径共用 |
| `glossary` | `assets/glossary.txt` | 术语表，`原文=译文` 每行一条 |

## 路径解析

- `pyvideotrans_home`、`index_tts2_home`、`glossary`：相对技能根目录。
- `src_dir`、`out_dir`：相对技能根目录；推荐写绝对路径。
- `work_dir`：相对 `out_dir`（默认 `<out>/_work`）。
- 成品：`<out>/<stem><final_suffix>.mp4`；字幕：`<out>/_字幕/`；状态：技能内 `.runtime/state/`；日志：技能内 `.runtime/logs/`。

## `setup` 写入的内容

1. 创建 `.runtime/logs/`、`.runtime/state/`、`tools/`。
2. 技能内 vendor 时安装依赖与模型；外部安装（`--reuse-pvt` / `--reuse-itt`）只跳过安装。
3. 写 `<pyvideotrans_home>/videotrans/params.json`：
   - `translate_type=5`（DeepSeek）
   - `deepseek_key`（来自 `--deepseek-key` 或 `DEEPSEEK_API_KEY`；已有值会保留）
   - `deepseek_model`
   - `tts_type=12`（index 后端）或 `1`（qwen3local）
   - `index_url`、`indextts_version=0`、`model_name` / `stt_model_name`
4. 写 `config.json`，最后运行 `doctor`。

> `params.json` 只保存在本机，不要提交或随包分发。

## 环境变量

| 变量 | 作用 |
|---|---|
| `DEEPSEEK_API_KEY` | `setup` 读取；`doctor` 也用它判断 Key 是否存在 |
| `PYVIDEOTRANS_HOME` | `pipeline.py` 为子进程设置，指向配置的 pyVideoTrans |
| `INDEXTTS2_HOME` | `pipeline.py` 为子进程设置，指向配置的 IndexTTS2 |
| `INDEXTTS2_FAST` | `serve` 由 `index_fast` 设置；`1` = beam 1 |
| `INDEXTTS2_RESTART_EVERY` | `serve` 由 `index_restart_every` 设置 |
| `INDEXTTS2_API` | `serve` 传给桥接的 API 地址 |
| `INDEXTTS2_CLI` / `SHIM_PORT` | `serve` 传给桥接的 CLI 路径/端口 |
| `VTV_CLONE_REF_MODE` | `single` / `line` / `cluster`，对应 `clone_ref_mode` |
| `VTV_SPK_MAX` / `VTV_SPK_THRESHOLD` | 说话人聚类参数，对应 `speaker_max` / `speaker_threshold` |
| `VTV_SPK_MERGE_SIM` / `VTV_SPK_SELF_CONSISTENCY` | 小簇合并与单句噪声判据，对应 `speaker_merge_sim` / `speaker_self_consistency` |
| `VTV_RESTART_BEFORE_TTS` | `1` 时在 prepare 后重启 daemon，对应 `restart_before_tts` |
| `VTV_DUBBING_CACHE` | 覆盖逐句配音缓存目录；A/B 或强制重新合成时指向空目录 |
| `VTV_MAX_AUDIO_SPEED_RATE` / `VTV_MAX_AUDIO_SLOW_RATE` | 对应 `max_audio_speed_rate` / `max_audio_slow_rate` |
| `VTV_DUBB_MIN_PAUSE_MS` | 对应 `dubb_min_pause_ms` |
| `VTV_FIT_MERGE` / `VTV_FIT_CONDENSE` | `1`/`0`，对应 `fit_merge` / `fit_condense` |
| `VTV_FIT_FORCE` | `1` 时非 IndexTTS2 也启用 fit（需先标定预测模型） |
| `VTV_FIT_MAX_SPEED` / `VTV_FIT_MAX_CHARS` | 对应 `fit_max_speed` / `fit_max_chars` |
| `VTV_FIT_GAP_MS` | 对应 `fit_gap_ms` |
| `VTV_FIT_PRED_A` / `VTV_FIT_PRED_B` | 对应 `fit_pred_a` / `fit_pred_b` |
| `VTV_FIT_CONDENSE_SPEED` | 对应 `fit_condense_speed` |
| `VTV_GLOSSARY_FILE` | 压缩改写时要求保留的术语表；`pipeline.py` 自动设为 `glossary` |
| `VTV_PIN_DAEMON` | `1` 时把真 daemon 固定 P-core + High，对应 `pin_daemon` |
| `VTV_ITT_PYTHON` | 聚类脚本使用的 IndexTTS2 Python（`pipeline.py` 自动设置） |
| `HF_ENDPOINT` | 强制 Hugging Face 镜像，如 `https://hf-mirror.com` |
| `TRANS_ASR_MODEL` | 覆盖 `trans_cli.py` 的默认 ASR 模型 |
| `HTTPS_PROXY` / `--proxy` | 依赖下载与模型下载的代理 |

## GPU 锁频任务（本机实测）

本机（RTX 5070 Ti Laptop + i7-14650HX）上，GPU 跑到 ~2700MHz 时会把 CPU 挤到 22%，
GPT 从 3.6s/句退化到 15–19s/句；把 GPU 上限锁到 1200MHz 后 CPU 回到 ~175%，
GPT 3.6s/句、总耗时 5.3–5.9s/句，100 句连续无退化。

注册登录自动锁频（需要一次管理员权限）：

```bat
scripts\gpu_lock_task.cmd
```

它会创建计划任务 `video-translate-gpu-clock`，登录时执行
`nvidia-smi -lgc 300,1200`。撤销并恢复默认：

```bat
scripts\gpu_lock_task.cmd remove
```

锁频期间游戏/3D 渲染性能会受限；不跑 TTS 时可随时 `nvidia-smi -rgc` 恢复。

## 常见改动

```jsonc
// 复用外部安装
{ "pyvideotrans_home": "D:/tools/pyvideotrans",
  "index_tts2_home": "D:/tools/index-tts2-cli" }

// 软字幕 + 其他语言
{ "source_lang": "ja", "target_lang": "en",
  "subtitle_mode": "soft", "glossary": "assets/glossary-ja-en.txt" }

// 更快但韵律略降
{ "index_fast": true, "index_restart_every": 50 }

// 保留逐句字幕，关闭配音单元合并/压缩（短句加速会回来）
{ "fit_merge": false, "fit_condense": false }

// 短句更紧凑：允许放慢到 1.3×，停顿降到 150ms
{ "max_audio_slow_rate": 1.3, "dubb_min_pause_ms": 150 }
```

改完运行 `python scripts/pipeline.py doctor` 验证。
