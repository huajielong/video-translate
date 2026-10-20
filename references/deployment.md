# 部署与迁移

## 环境要求

| 项 | 推荐 |
|---|---|
| OS | Windows 11 / Linux x86_64（macOS 可跑但未验证） |
| GPU | NVIDIA，≥12 GB 显存（IndexTTS2 约 7.6 GB） |
| RAM | ≥16 GB（32 GB 更稳） |
| 磁盘 | ≥20 GB（IndexTTS2 权重 8.5 GB + 两个 venv + 模型缓存） |
| Python | 3.10+（`pipeline.py` 仅标准库；venv 由 uv 创建） |
| 网络 | PyPI 镜像/代理；Hugging Face/ModelScope 可访问 |

## 分发

> `vendor/index-tts2-cli` 是运行时副本，不是第二个技能：保留 `scripts/`、`indextts/`、`references/`，已移除其独立的 `SKILL.md` / `CLAUDE.md`，避免出现第二个技能入口。需要单独使用 TTS 时请安装 `index-tts2-cli` 技能本身。

**只复制技能目录**，不要复制：

- `vendor/pyvideotrans/.venv`（Windows venv 含绝对路径）
- `vendor/pyvideotrans/models`、`tmp`、`output`、`logs`
- `vendor/index-tts2-cli/checkpoints`、`venv`、`runtime`、`out`
- `config.json`、`.runtime/`、`work/`
- 任何 `*_key` / `params.json` 中的密钥

> `config.json` 可能保留着当前机器的外部安装路径。复制到新机器前请删除它，
> 或在目标机用 `setup` 覆盖。

目标机：

```bash
# 新装（下载依赖与模型）
python scripts/pipeline.py setup

# 或复用已有安装
python scripts/pipeline.py setup \
  --reuse-pvt /path/to/pyvideotrans \
  --reuse-itt /path/to/index-tts2-cli
```

`setup` 会：

1. 用 `trans_cli.py setup` 创建 pyVideoTrans venv、uv、ffmpeg、Whisper large-v3-turbo；
2. 用 `indextts2.py setup` 创建 IndexTTS2 venv 并下载 checkpoints；
3. 检查 Rubberband（Windows 包内已带可执行文件）；
4. 写 `params.json` 与 `config.json`（字段含义见 `configuration.md`）；
5. 运行 `doctor`。

> `doctor` 只有源码与 venv 缺失时返回非 0；Rubberband、API、Key 缺失只报警告。

## 网络

- PyPI：`setup` 默认清华镜像；代理可用 `--proxy http://127.0.0.1:2080` 或 `HTTPS_PROXY`。
- 模型：IndexTTS2/Whisper 优先 ModelScope，失败回落 HF 镜像；见 `vendor/index-tts2-cli/references/deployment.md`。
- 国内直连 Hugging Face 常失败；准备代理或镜像。

## 端口与安全

- IndexTTS2 API：`127.0.0.1:8791`
- Gradio 桥接：`127.0.0.1:7861`
- 默认只监听本机；不要暴露到公网。`serve` 不设鉴权，公网部署需加反向代理与鉴权。
- DeepSeek Key 只写入本机 `params.json`，不要提交到仓库；端口/路径等全部配置项见 `configuration.md`。

## Linux / macOS

- Linux x86_64：与 Windows 流程一致；Rubberband 用 `apt install rubberband-cli`；venv 路径为 `.venv/bin/python` / `venv/bin/python`。
- macOS Apple Silicon：IndexTTS2 可用 MPS（较慢）；Rubberband `brew install rubberband`；`pynini` 无 ARM 轮子时按上游 setup 自动跳过（仅影响 MOSS TTS 文本规范化）。
- 未在 macOS 实机验证，首次使用请先跑 `doctor` 和一条 30 秒样片。

## 更新

- 替换 `vendor/pyvideotrans` 或 `vendor/index-tts2-cli` 后，重新跑 `setup`（依赖/模型已缓存则秒回）。
- 本项目对 vendor 的补丁见 `references/quality.md` 与 `references/pipeline.md`；升级上游后需重新应用：
  - `_rate.py` concat 强制 48k/2ch
  - `_stage_dubbing.py` 按时间重叠配对、逐行缓存
  - `_stage_translate.py` / `translate_srt.py` 超长块拆分
  - `_srt_wrap.py` 平衡两行、术语不拆
  - `_qwenttslocal.py` 纯 clone 只要求 Base
  - `cli.py` 修复 `video_autorate` 未写入
