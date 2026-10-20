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
| `index_restart_every` | `25` | 每 N 句重启守护进程；`0` 关闭 |
| `mt_workers` | `4` | 翻译并发（纯网络等待） |
| `loudness` | `-18.0` | 成品集成响度（LUFS） |
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
| `VTV_CLONE_REF_MODE` | `single` 时整片复用一个参考音频（默认逐句参考） |
| `HF_ENDPOINT` | 强制 Hugging Face 镜像，如 `https://hf-mirror.com` |
| `TRANS_ASR_MODEL` | 覆盖 `trans_cli.py` 的默认 ASR 模型 |
| `HTTPS_PROXY` / `--proxy` | 依赖下载与模型下载的代理 |

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
```

改完运行 `python scripts/pipeline.py doctor` 验证。
