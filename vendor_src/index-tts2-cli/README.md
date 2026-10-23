# IndexTTS2 CLI

本地部署的 **IndexTTS2**（Bilibili 零样本语音合成）命令行工具 + 浏览器工作台 + HTTP API。

- 零样本音色克隆：用一段 3~15 秒参考音频复制音色
- 四种情感控制：音色自带 / 情感参考音频 / 八维向量 / 文字描述
- 中英文混合、拼音注音、长文本自动分段
- **常驻守护进程**：模型只加载一次，之后每次合成为秒级
- `webui`：零依赖浏览器工作台（选音色、调参、波形、试听、历史）
- `api`：OpenAI 兼容 + 原生 HTTP API，支持异步任务、鉴权、CORS、Range
- Windows / Linux / Docker 可用；macOS Apple Silicon 实验性支持

> 本项目是社区**非官方（unofficial）封装**，与 Bilibili 无隶属关系。IndexTTS2 模型与上游代码受 bilibili Model Use License 约束，详见 [许可与致谢](#13-许可与致谢)。
>
> 面向智能体的完整使用说明在 [`SKILL.md`](SKILL.md)，更细的参数、部署与排障见 [`references/`](references/)。

---

## 目录

- [Agent 安装与使用](#agent-安装与使用)

1. [环境要求](#1-环境要求)
2. [安装](#2-安装)
3. [快速开始](#3-快速开始)
4. [常用命令](#4-常用命令)
5. [情感控制](#5-情感控制)
6. [音色库](#6-音色库)
7. [浏览器工作台 WebUI](#7-浏览器工作台-webui)
8. [HTTP API](#8-http-api)
9. [配置与环境变量](#9-配置与环境变量)
10. [目录结构](#10-目录结构)
11. [故障排查](#11-故障排查)
12. [文档索引](#12-文档索引)
13. [许可与致谢](#13-许可与致谢)

---

## Agent 安装与使用

本节面向通过脚本、工具调用或 MCP 封装驱动本 CLI 的智能体。人类用户从第 1 节开始；面向智能体的完整契约在 [`SKILL.md`](SKILL.md)。

### A.1 安装与自检

智能体应使用启动器而不是直接猜 Python 解释器：

- Windows：`scripts\indextts2.cmd`
- bash / Linux / macOS：`bash scripts/indextts2.sh`

先做一次快速自检：

```bat
REM Windows
scripts\indextts2.cmd doctor --quick --json
```

```bash
# Linux / bash
bash scripts/indextts2.sh doctor --quick --json
```

`doctor --quick --json` 不 import torch，秒级返回，检查项目目录、解释器、模型文件、ffmpeg、缓存目录、状态目录可写性。解析返回的 `ok` 和 `checks`；失败项看 `detail`。

如果 `venv/` 或 `checkpoints/` 缺失，先安装 uv（Windows 还需 Python 3.10/3.11），再显式补齐：

```bat
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
scripts\indextts2.cmd setup --json
scripts\indextts2.cmd doctor --json
```

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
bash scripts/indextts2.sh setup --json
bash scripts/indextts2.sh doctor --json
```

- `setup` 可能下载约 8.5 GB 模型、建立约 9 GB 的 Python 环境，耗时可能几十分钟。**不要中断、不要并发重试**；给它一个长超时（例如 3600 秒）。
- 资源齐备时 `setup` 只做本地检查、不联网。
- `setup` 成功后，首次 `say` 仍会加载模型（约 25~45 秒），之后才快。
- Docker 环境的安装/一次性调用见第 2.4 节 Docker；长驻容器用 `serve` + `docker exec`，或直接用 WebUI/API 的 8790/8791。

所有命令都是非交互的；不要传需要人工确认的参数。

### A.2 调用协议（必须遵守）

1. **始终加 `--json`**。stdout 恰好一行 JSON（`schema` 命令是例外，见 A.4）；进度和提示都在 stderr。
2. **不要合并 stderr 到 stdout**。先解析 stdout 的那一行，再决定是否需要读 stderr 的进度。
3. JSON 是纯 ASCII（中文以 `\uXXXX` 表示），直接 `json.loads` 即可。
4. 先检查 `ok`；成功时读取 `output_path`、`audio_seconds`、`params`、`daemon`。
5. **必须检查 `warnings`**。`ok: true` 不代表音频完整：`max_mel_tokens` 截断等静默劣化只会出现在这里。
6. 失败时读取 `error.code`、`error.message`、`error.hint`、`error.retriable`；`error.log_tail` 已内联守护进程日志尾部。`hint` 通常是可执行的下一步，优先照做。
7. **第一次调用约 25~45 秒**（加载模型）。`daemon.was_cold_start: true` 表示成功付了冷启动成本，**不要因为慢而重试**。之后每次约 5~10 秒。
8. `--json` 可以写在命令前或命令后；`--pretty` 只给人看，机器解析不要用。
9. 进程超时至少给 120 秒（首次调用），长文本用后台任务或 `--timeout`。
10. 不要例行使用 `--no-daemon`：它会每次重新加载模型，只用于排障或一次性脚本。

退出码是稳定的分流信号：

| 退出码 | 含义 | 智能体动作 |
|---|---|---|
| 0 | 成功 | 检查 `warnings`，读 `output_path` |
| 2 | 用法/参数错误 | 修正参数；`indextts2 schema` 查看可用项 |
| 3 | 音色不存在/不可读 | `voices list --json` 重新选音色 |
| 4 | 守护进程启动失败/超时；WebUI/API 端口占用 | `doctor` / `status` / 看 `daemon.log`；最多重试一次 |
| 5 | 队列已满 | 稍后重试，或先 `wait` 已有任务 |
| 6 | 显存不足 | 用 `--fast`、调小 `max_text_tokens_per_segment`、拆短文本，或 `stop` 后重试 |
| 7 | 推理失败 | 看 `error.hint` 与 `log_tail`；不要盲目重试 |
| 8 | 守护进程被 CUDA 错误毒化后退役 | 客户端会自动重启，最多重试一次 |
| 9 | 被强制中断 | 检查任务是否需要重提 |
| 130 | Ctrl+C | 由调用方决定是否重试 |

### A.3 标准合成流程

```bash
# 1) 先看有哪些音色，不要猜名字
indextts2 voices list --json

# 2) 可选：秒级估时，不加载模型
indextts2 estimate --text-file input.txt --json

# 3) 同步合成（短文本）
indextts2 say --text-file input.txt --voice voice_01 --json
```

- `--voice` 接受音色名、内容 id 或音频路径。**不要凭空猜音色名**；先 `voices list --json`，或用 `examples/` 里已注册的 `voice_01`…`voice_12`。
- 长文本一律用 `--text-file <UTF-8 文件>`，不要把中文长文本硬塞进命令行：Windows 控制台/shell 的多层转义很容易破坏引号、`!`、`&`、`%`。
- `--output`、`--text-file`、`--voice` 的文件路径建议用**绝对路径**，避免不同工作目录下的解析歧义。
- 输出固定为 22050 Hz / 单声道 / 16-bit PCM WAV。

预计超过 60 秒时用后台任务：

```bash
indextts2 --timeout 7200 say --text-file long.txt --voice voice_01 --background --json
# → {"ok":true,"job_id":"j_...","state":"queued","queue_position":0,...}

indextts2 wait j_abc123def456 --json
# 如果还需要更久：
indextts2 wait j_abc123def456 --timeout 7200 --json
```

- `wait` 超时**不会取消任务**；任务仍在守护进程里执行，继续 `wait` 同一个 `job_id` 即可。
- 全局 `--timeout` 要写在子命令**前面**（`indextts2 --timeout 7200 say ...`）；`wait` 自己的 `--timeout` 写在 `wait` 后面。

### A.4 参数、情感与 schema

不要猜参数名/范围。需要自省时：

```bash
indextts2 schema
```

`schema` 是**例外**：它不包装成一行 JSON，而是输出一份缩进的、纯机器可读的 JSON 文档（包含情感模式、八维分量顺序、全部生成参数及 `min/max/default/step`、被拒绝的参数）。解析时用整个 stdout，不要按“最后一行”解析。

常用表达：

```bash
# 模式 1：情感参考音频
indextts2 say --text-file in.txt --voice voice_01 --emo-audio emo_sad --emo-alpha 0.65 --json

# 模式 2：八维向量（也可写 0,0,0.8,0,0,0,0,0）
indextts2 say --text-file in.txt --voice voice_01 --emo-vec 哀=0.8 --json

# 模式 3：情感描述文本
indextts2 say --text-file in.txt --voice voice_01 --emo-text "委屈巴巴" --json
```

- 三种情感模式**互斥**；同时给会返回 `BAD_USAGE`，不要静默丢弃。
- 模式 2 的 `emo_vec` 是 8 个 0~1 的分量，顺序：喜/怒/哀/惧/厌恶/低落/惊喜/平静；服务端会做 bias 归一化并在 `params.emo_vector` 回显。
- `--fast` 等价于 `num_beams=1`，约快 3 倍；对时间敏感的调用优先用。
- 结果里的 `params` 是真正生效的参数回显；需要复现时把 `seed` 一起记录。

### A.5 最小 Python 封装

```python
import json
import os
import subprocess

ROOT = os.environ.get("INDEXTTS2_HOME", os.path.abspath("."))  # 指向项目根目录

if os.name == "nt":
    CLI = ["cmd", "/c", os.path.join(ROOT, "scripts", "indextts2.cmd")]
else:
    CLI = ["bash", os.path.join(ROOT, "scripts", "indextts2.sh")]


def cli(*args: str, timeout: float = 300) -> dict:
    proc = subprocess.run(
        [*CLI, *args, "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(
            f"no JSON output; exit={proc.returncode}; stderr={proc.stderr[-1000:]}"
        )
    payload = json.loads(lines[-1])
    if not payload.get("ok"):
        error = payload.get("error") or {}
        raise RuntimeError(
            f"{error.get('code')}: {error.get('message')} "
            f"hint={error.get('hint')}"
        )
    return payload


if __name__ == "__main__":
    result = cli("say", "--text-file", "/abs/path/input.txt", "--voice", "voice_01")
    print(result["output_path"])
    print(result.get("warnings"))
```

说明：

- 这是**短任务**封装，默认 300 秒；首次冷启动可能需要更久，调用方按需调大。长任务请用 `say --background` + `wait`，不要把 `cli()` 阻塞几十分钟。
- `schema` 命令不要用这个解析器（它输出多行 JSON）；直接 `json.loads(proc.stdout)`。
- 封装只读 stdout；stderr 的进度日志可选择性记录，但不要当成结果。
- Windows 用 `cmd /c` 保证 `.cmd` 启动器可执行；Linux 用 `bash`。

### A.6 错误恢复策略

```text
exit 2  → 修参数，必要时 `indextts2 schema`
exit 3  → `indextts2 voices list --json`，换合法音色
exit 4  → `indextts2 doctor --quick --json` / `status --json`；
          看 `daemon.log` 末尾；最多重试一次
exit 5  → 队列满：`wait` 已有任务，或退避后重试
exit 6  → 显存不足：`--fast`、调小 max_text_tokens_per_segment、
          拆短文本、`indextts2 stop` 后重试
exit 7  → 看 error.hint / error.log_tail；不要盲目重试
exit 8  → 守护进程退役：客户端会自动换新进程；最多重试一次
exit 130/9 → 由调用方决定是否重提
```

- `error.retriable: true` 才适合自动重试；`false` 先修参数/环境。
- `ok: true` 也要检查 `warnings`，尤其是 `max_mel_tokens` 截断。
- `daemon.was_cold_start: true` 是正常成功，不要重试。
- 并发调用会进入守护进程的串行队列（默认上限 4）；不要用并发“加速”，那只会排队。
- `stop` 会影响所有共享该守护进程的调用方；只有在明确需要释放显存或排障时使用。

### A.7 长驻服务模式（可选）

大多数智能体直接用 CLI 即可。需要连续处理多个请求、或通过 HTTP 集成时，用 `api`：

```bash
# 用 Bash 工具的 run_in_background，或交给进程管理器；不要在前台阻塞
indextts2 api --listen-port 8791 --no-preload --json
```

- stdout 第一行是启动 JSON（`service`/`url`/`port`/`auth_required`/`preload`），之后进程常驻；日志在 stderr。
- HTTP API 的 JSON 响应是 UTF-8（与 CLI 的纯 ASCII `--json` 不同）；直接按 UTF-8 解码。
- 用 `GET /health` 检查服务与守护进程状态；合成端点见 [8. HTTP API](#8-http-api)。
- 对外暴露时设置 `INDEXTTS2_API_KEY`（不要写进命令行），并只监听需要的地址；默认 `127.0.0.1`。
- OpenAI 端点默认 `response_format=mp3`，需要 ffmpeg；不想转码就传 `response_format=wav`。
- `api`/`webui` 都是前台服务；Ctrl+C 只停前端，不停守护进程。`--listen-port` 是前端端口，`--port` 是守护进程端口。
- **不要**在无头环境里用 `webui --open`；WebUI 是给人用的。智能体优先用 CLI 或 API。

### A.8 不要做的事

- 不要猜音色名/音色 id；先 `voices list --json`。
- 不要把长中文文本作为 shell 参数；用 UTF-8 文件 + `--text-file`。
- 不要因为首次调用慢就重试（`was_cold_start`）。
- 不要忽略 `warnings`。
- 不要把 stderr 当作结果，也不要用 `--pretty` 做机器解析。
- 不要例行使用 `--no-daemon`。
- 不要在别的调用方可能正在使用时随意 `stop`。
- 不要把 WebUI/API 直接暴露到 `0.0.0.0` 而不加鉴权/反向代理。
- 不要把 API key 写进命令行、日志或仓库。

---

## 1. 环境要求

| 项目 | 要求 |
|---|---|
| GPU | NVIDIA，建议显存 ≥ 12 GB（fp16 实测约 7.6 GB） |
| 驱动 | 较新的 NVIDIA 驱动（cu128 构建；Windows 建议 ≥ 570.65，Linux ≥ 570.26） |
| 磁盘 | 模型 8.5 GB + Python 环境约 9 GB，建议预留 30 GB 以上 |
| 系统 | Windows 10/11、Linux x86_64；macOS Apple Silicon 实验性支持 |
| Python | 3.10 / 3.11（项目 `.python-version` 为 3.10） |
| ffmpeg | Linux/macOS 需要安装；Windows 发行包自带 `venv\ffmpeg.exe`；Docker 镜像已内置 |
| 网络 | 首次补齐模型时需要；之后默认离线运行 |

> 如果本目录已经带有 `venv\` 和 `checkpoints\`（Windows 发行包通常如此），一般不需要手动建环境或下模型。  
> 从源码 clone、或把项目拷到 Linux 时，才需要执行下面的 `setup`。

---

## 2. 安装

### 2.1 Windows（推荐，开箱即用）

1. 确认已安装 NVIDIA 驱动。
2. 把整个项目目录放到本地磁盘，例如 `D:\index-tts2-cli`（**不要**放在 OneDrive 同步盘或网络盘）。
3. 打开 PowerShell 或 cmd，进入项目目录：

```bat
cd /d D:\index-tts2-cli
scripts\indextts2.cmd doctor
```

`doctor` 全部通过后即可合成：

```bat
scripts\indextts2.cmd say "你好，欢迎使用 IndexTTS2。" --voice voice_01 --json
```

如果 `venv\` 或 `checkpoints\` 缺失（例如从源码 clone），先安装 Python 3.10/3.11 和 [uv](https://docs.astral.sh/uv/)，然后：

```bat
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
scripts\indextts2.cmd setup
```

`setup` 会自动建立环境并补齐缺失的模型文件；已经存在的文件不会重复下载。

### 2.2 Linux（x86_64 + NVIDIA）

```bash
# 1) 安装 uv（用于创建 Python 3.10 环境和锁定的依赖）
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"

# 2) 安装 ffmpeg（libsndfile 等系统库通常已具备；缺失时安装）
sudo apt-get update && sudo apt-get install -y ffmpeg

# 3) 进入项目目录
cd ~/index-tts2-cli

# 4) 建环境 + 补齐模型（模型已拷过来的话这一步不联网）
bash scripts/indextts2.sh setup

# 5) 自检 + 合成
bash scripts/indextts2.sh doctor
bash scripts/indextts2.sh say "Linux 上跑通了。" --voice voice_01 -o /tmp/a.wav
```

说明：

- 系统 Python 是 3.12 也没关系：`.python-version` 指定 3.10，uv 会自动下载托管的 CPython 3.10。
- Linux 使用 `WeTextProcessing`（依赖 `pynini`），与 Windows/macOS 的 `wetext` 由依赖 marker 自动区分。
- **ARM64 Linux 不支持**：`pynini` 与 cu128 都没有 aarch64 wheel。
- **不要把 `checkpoints/` 放在网络盘或 WSL 的 `/mnt/` 上**：同一份模型在 `/mnt/d` 上加载可能需要 180 秒，本地 ext4 约 25 秒。WSL 里建议先 `cp` 到 `~` 下。
- 国内加速：
  ```bash
  export INDEXTTS2_PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple
  export HF_ENDPOINT=https://hf-mirror.com
  ```

### 2.3 macOS（Apple Silicon，实验性）

```bash
brew install uv ffmpeg
cd ~/index-tts2-cli
bash scripts/indextts2.sh setup
bash scripts/indextts2.sh doctor      # 应显示 MPS
bash scripts/indextts2.sh say "你好" --voice voice_01 -o /tmp/a.wav
```

- 只支持 Apple Silicon；`torch 2.8` 没有 macOS x86_64 wheel。
- 建议 24 GB 以上内存（权重 8.5 GB + fp32 激活 + KV cache）；16 GB 会大量 swap。
- 部分算子没有 MPS 实现，启动时会自动设置 `PYTORCH_ENABLE_MPS_FALLBACK=1`。
- 失败时可 `export INDEXTTS2_DEVICE=cpu`（非常慢，但能验证链路）。
- 未在本项目真机验证，仅验证过依赖解析。

### 2.4 Docker（linux/amd64 + NVIDIA）

镜像**不含模型**（约 21 GB：CUDA 基础镜像 + Python 环境 + 运行时）。模型用 volume 挂载，或首次进容器 `setup` 下载。

```bash
docker build -t indextts2:cu128 .

# 一次性合成（入口会自动注入 --no-daemon）
docker run --rm --gpus all \
  -v /host/checkpoints:/app/checkpoints \
  -v "$PWD/out:/data/out" \
  indextts2:cu128 say "容器里合成。" --voice voice_01 -o /data/out/a.wav

# 长驻服务 + docker exec（多次合成推荐）
docker run -d --name tts --gpus all --restart unless-stopped \
  -v /host/checkpoints:/app/checkpoints \
  -v tts-state:/var/lib/indextts2 \
  -v "$PWD/out:/data/out" \
  indextts2:cu128 serve

docker exec -i tts /opt/venv/bin/python /app/scripts/indextts2.py \
  say "第二次调用。" --voice voice_01 -o /data/out/b.wav
```

国内构建（apt 与 uv 分开控制，避免代理抖动拖垮整层）：

```bash
docker build \
  --build-arg APT_MIRROR=https://mirrors.aliyun.com/ubuntu \
  --build-arg UV_PROXY=http://host.docker.internal:7890 \
  -t indextts2:cu128 .
```

> **不要用 `-p 8788:8788`**：守护进程只监听容器内的 `127.0.0.1`，端口映射访问不到（这是刻意的安全设计）。容器内用 `docker exec`；需要对外服务请用 WebUI/API 的 8790/8791（见第 7、8 节）。

Git Bash 里执行 Docker 命令前建议 `export MSYS_NO_PATHCONV=1`，否则 `/data/out` 这类容器内绝对路径可能被改写成 Windows 路径。

### 2.5 模型文件

首次运行 `say` / `serve` / `webui` / `api` 时，如果 `checkpoints/` 或 Hugging Face 缓存缺失，且 `config.json` 的 `auto_download` 为 `true`（默认），会自动补齐；资源齐备时只做本地文件检查，**完全不联网**。

也可以手动检查/补齐：

```bash
# 检查并补齐模型权重（HF 失败会自动回退 ModelScope）
indextts2 setup

# 只诊断，不下载
indextts2 doctor

# 允许联网补齐缺失的 Hugging Face 权重
indextts2 doctor --fetch
```

模型总量约 8.5 GB：

- `checkpoints/`：gpt.pth、s2mel.pth、qwen 情感模型、bpe.model、feat1/2.pt 等（5.49 GB）
- `checkpoints/hf_cache/`：w2v-bert、MaskGCT semantic_codec、campplus、BigVGAN（2.8 GB）

如果已有完整 `checkpoints/`，直接拷过去即可，不需要重新下载。`config.json` 里把 `auto_download` 设为 `false` 可以禁止自动下载（缺失时直接报错）。

---

## 3. 快速开始

以下的 `indextts2` 代指：

- Windows：`scripts\indextts2.cmd`
- bash / Git Bash / Linux / macOS：`bash scripts/indextts2.sh`

### 3.1 第一次合成

```bash
indextts2 say "你好，欢迎使用 IndexTTS2。" --voice voice_01 --json
```

首次调用会在后台拉起守护进程并加载模型，约 **30~45 秒**；之后每次约 **6 秒**。

成功时 stdout 恰好一行 JSON（全 ASCII）：

```json
{"ok": true, "output_path": "D:\\...\\indextts2_20261019_210109_8444.wav", "audio_seconds": 3.23, "warnings": [], "daemon": {"was_cold_start": true}}
```

读 `output_path` 拿音频（固定 22050 Hz / 单声道 / 16-bit PCM）。进度和提示都在 stderr。

> **`ok: true` 不等于音频完整**：`max_mel_tokens` 截断等静默劣化只会出现在 `warnings` 里。每次都应检查这个字段。

### 3.2 常用写法

```bash
# 指定输出路径
indextts2 say "你好" --voice voice_01 -o D:\out\a.wav

# 长文本：强烈建议走文件，避免中文引号/特殊符号在多层 shell 转义中走样
indextts2 say --text-file script.txt --voice voice_01 --json

# 从 stdin 读
echo "从管道读也行" | indextts2 say --text-file - --voice voice_01 --json

# 后台提交 + 稍后收结果（预计超过 60 秒时使用）
indextts2 say --text-file long.txt --voice voice_01 --background --json
indextts2 wait j_abc123def456 --json

# 秒级估时，不加载模型
indextts2 estimate --text-file long.txt --json

# 快速模式：beam 3 → 1，约快 3 倍
indextts2 say "快速合成" --voice voice_01 --fast
```

### 3.3 试听与查看音色

```bash
indextts2 voices list --json
indextts2 voices preview voice_05 --json
```

`examples/` 下的 `voice_01`…`voice_12`、`emo_sad`、`emo_hate` 已自动注册，开箱可用。`--voice` 也接受内容 id 或直接给音频文件路径。

---

## 4. 常用命令

```
indextts2 [全局选项] <命令> [命令选项]

say       把文本合成为语音
wait      等待一个后台任务并取回结果
voices    音色库：list / add / rm / rename / preview
serve     前台运行守护进程
stop      停止守护进程（--force 强制）
status    守护进程状态 / 显存 / 队列
doctor    环境自检
estimate  只算分句与预估耗时，不加载模型
schema    输出机器可读的参数说明
webui     启动浏览器工作台（默认 127.0.0.1:8790）
api       启动 HTTP API（默认 127.0.0.1:8791）
```

全局选项：

| 选项 | 说明 |
|---|---|
| `--json` | stdout 只输出一行 JSON（可写在命令前或命令后） |
| `--pretty` | 配合 `--json`，缩进输出 |
| `--timeout <秒>` | 客户端等待任务的秒数上限（默认 3600） |
| `--project-dir <路径>` | 覆盖项目目录 |
| `--port <端口>` | **守护进程**端口（默认 8788） |
| `--allow-network` | 允许 Hugging Face 联网 |

守护进程管理：

```bash
indextts2 status
indextts2 doctor
indextts2 stop          # 等当前任务完成后退出
indextts2 stop --force  # 立即终止（打断推理）
indextts2 setup
indextts2 schema
```

退出码可以直接分流：

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 用法/参数错误 |
| 3 | 音色不存在或不可读 |
| 4 | 守护进程启动失败/超时，或 WebUI/API 端口占用 |
| 5 | 队列已满 |
| 6 | CUDA 显存不足 |
| 7 | 推理失败 |
| 8 | 守护进程被 CUDA 错误毒化后退役 |
| 9 | 被强制中断 |
| 130 | Ctrl+C |

失败 JSON 里的 `error.hint` 是一条可执行的下一步，`error.log_tail` 已内联 `daemon.log` 尾部。

---

## 5. 情感控制

不传情感参数时是**模式 0：音色即情感**。三种控制方式**互斥**（同时给会直接报错）。

```bash
# 模式 1：用另一段音频当情感参考（默认强度 0.65）
indextts2 say "文本" --voice voice_01 --emo-audio emo_sad --emo-alpha 0.65

# 模式 2：八维向量，顺序 喜/怒/哀/惧/厌恶/低落/惊喜/平静（默认强度 1.0）
indextts2 say "文本" --voice voice_01 --emo-vec 哀=0.8
indextts2 say "文本" --voice voice_01 --emo-vec 0,0,0.8,0,0,0,0,0

# 模式 3：情感描述文本（默认强度 0.6，多花约 30% 时间）
indextts2 say "文本" --voice voice_01 --emo-text "委屈巴巴"
```

| 模式 | 触发 | `--emo-alpha` 默认 |
|---|---|---|
| 0 音色即情感 | 不传情感参数 | 忽略（上游强制 1.0） |
| 1 情感参考音频 | `--emo-audio <音色/路径>` | 0.65 |
| 2 八维向量 | `--emo-vec <向量>` | 1.0 |
| 3 情感描述文本 | `--emo-text "<描述>"` | 0.6 |

八维向量支持三种写法：位置写法（正好 8 个数）、命名写法（`哀=0.8`，未提及的分量记 0）、两种混用。分量取值 0~1。服务端会做 bias 归一化：每个分量乘 bias，总和超过 0.8 时按比例缩放，再乘 `emo_alpha`。结果里的 `params.emo_vector` 回显最终值。

完整规则见 [`references/emotion.md`](references/emotion.md)。

---

## 6. 音色库

```bash
indextts2 voices list --json
indextts2 voices add 旁白女声 D:\samples\a.wav --notes "女声，语气平稳" --json
indextts2 voices rename 旁白女声 narrator
indextts2 voices rm narrator
indextts2 voices preview narrator --json
```

- 注册表在 `<项目>/voices/voices.json`。
- `voices add` 会把音频**复制**进 `<项目>/voices/`；`voices rm` 只删除复制进来的那份，不动原始文件。
- 参考音频超过 15 秒会被上游自动截断。
- 任何参考音频在送进模型前都会先归一化成单声道 16-bit PCM WAV，缓存在状态目录的 `voices/<sha1>.wav`，命中缓存时不会重复转码。
- `voices list` 还会自动发现 `<项目>/examples/`、`<项目>/prompts/`、`<项目>/voices/` 下的音频。

细节见 [`references/voices.md`](references/voices.md)。

---

## 7. 浏览器工作台 WebUI

```bash
indextts2 webui --open
```

默认打开 `http://127.0.0.1:8790`。它不依赖 gradio，也不访问任何外部 CDN/字体。

功能：

- 文本输入、中英文示例、`Ctrl/⌘ + Enter` 生成
- 音色下拉、试听、上传参考音频
- 四种情感模式 + 情感强度
- 高级参数、快速模式、seed、输出文件名
- 异步生成进度、排队位置、取消
- 真实波形、播放/下载、warnings、原始结果 JSON
- 最近生成历史（`runtime/out` 或 `INDEXTTS2_OUT_DIR`）

常用选项：

```bash
indextts2 webui --listen-port 9000   # 改 WebUI 端口（不是守护进程端口）
indextts2 webui --no-preload         # 打开页面时不预加载模型
indextts2 webui --host 0.0.0.0       # 对外开放（会警告没有内置登录）
```

启动器：

```bat
scripts\indextts2-webui.cmd --open
```

```bash
bash scripts/indextts2-webui.sh --open
```

默认只监听 `127.0.0.1`。WebUI **没有内置登录**，`--host 0.0.0.0` 时任何能访问该端口的人都能上传音色和发起合成；远程访问请放在 VPN 或带鉴权的反向代理后面。

WebUI 和 API 都是**前台常驻服务**：进程在，页面/接口就在。Ctrl+C 只停止前端服务，不停止守护进程；需要释放显存时执行 `indextts2 stop`。端口语义也不要混淆：`--listen-port` 是 WebUI/API 自己的端口，全局 `--port` 始终是守护进程端口。

Docker：

```bash
docker run --rm -p 8790:8790 --gpus all \
  -v /host/checkpoints:/app/checkpoints \
  -v /host/voices:/app/voices \
  -v /host/out:/data/out \
  indextts2:cu128 webui --host 0.0.0.0
```

挂载 `voices/` 才能从页面里上传音色（镜像层只读）。

完整说明见 [`references/webui.md`](references/webui.md)。

---

## 8. HTTP API

### 8.1 启动与鉴权

```bash
indextts2 api --api-key my-secret
```

默认 `http://127.0.0.1:8791`。

```bash
# 环境变量方式更适合常驻服务（不会出现在进程列表/shell history）
export INDEXTTS2_API_KEY=my-secret
indextts2 api

# 改 API 自己的监听端口（不是守护进程端口）
indextts2 api --listen-port 9001

# 浏览器跨域（前端直连 API 时）
indextts2 api --api-key my-secret --cors

# 不预加载模型、同步请求超时
indextts2 api --no-preload --request-timeout 600

# 对外开放（务必设 key）
indextts2 api --host 0.0.0.0 --api-key my-secret
```

`--listen-port` 是 API 自己的端口；全局 `--port` 仍是守护进程端口。API 同样是前台服务，Ctrl+C 只停 API，不停守护进程。

鉴权要求：

```
Authorization: Bearer <key>
# 或
X-API-Key: <key>
```

鉴权覆盖 `/v1/*`；`/health`、`/`、`/openapi.json` 免鉴权。未设置 key 时不鉴权；监听非本机地址且没有 key 时，启动会打印警告。

启动器：

```bat
scripts\indextts2-api.cmd --api-key my-secret
```

```bash
bash scripts/indextts2-api.sh --api-key my-secret
```

### 8.2 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 服务 + 守护进程快照 |
| GET | `/` | 端点索引 |
| GET | `/openapi.json` | OpenAPI 3 描述 |
| GET | `/v1/models` | OpenAI 风格模型列表 |
| POST | `/v1/audio/speech` | OpenAI 兼容，返回音频二进制 |
| POST | `/v1/tts` | 原生同步，返回结果 JSON |
| POST | `/v1/jobs` | 异步提交 → 202 |
| GET | `/v1/jobs/<job_id>` | 查询任务 |
| DELETE | `/v1/jobs/<job_id>` | 取消排队中的任务 |
| GET | `/v1/audio/<job_id>` | 任务音频（Range / `?download=1`） |
| GET | `/v1/audio-file/<name>` | 按文件名取输出目录内的 wav |
| GET | `/v1/voices` | 音色列表 |
| POST | `/v1/voices` | base64 上传并登记音色 |
| DELETE | `/v1/voices/<name>` | 删除已登记音色 |
| GET | `/v1/history?limit=20` | 最近产物（上限 100） |

### 8.3 原生同步合成

```bash
curl -s -X POST http://127.0.0.1:8791/v1/tts \
  -H "Authorization: Bearer my-secret" \
  -H "Content-Type: application/json" \
  -d '{"text":"你好，这是 API 测试。","voice":"voice_01","fast":true}'
```

请求字段：

| 字段 | 说明 |
|---|---|
| `text` / `input` | 必填；`input` 是 OpenAI 风格别名；上限 20000 字符 |
| `voice` | 音色名、内容 id 或本机音频路径；省略时用 `default_voice` → `voice_01` → 第一个音色 |
| `output_name` | 只能是纯文件名（固定 `.wav`），落在输出目录；重名自动追加短哈希 |
| `emo_mode` | `0` 音色自带 / `1` 情感参考 / `2` 八维向量 / `3` 文字描述 |
| `emo_audio` | 模式 1 的音色名或音频路径 |
| `emo_vec` | 模式 2：8 元数组、`{"哀":0.7}` 对象、或 `"哀=0.7"` 字符串 |
| `emo_text` | 模式 3 的描述；留空用正文 |
| `emo_alpha` | 情感强度 0~1 |
| `emo_random` | 模式 2 随机采样情感权重 |
| `seed` | 整数，用于复现 |
| `fast` | 等价于 `num_beams=1` |
| `params` | 生成参数对象（`top_p`/`top_k`/`temperature`/...）；也可平铺在顶层 |

响应与 `say --json` 字段一致，并额外带 `audio_url`：

```json
{
  "ok": true,
  "schema": 1,
  "job_id": "j_163a60fdd4cc",
  "output_path": "D:\\...\\api_20261019_210502_2216_0d0edd.wav",
  "audio_url": "/v1/audio/j_163a60fdd4cc",
  "audio_seconds": 2.276,
  "duration_s": 4.055,
  "rtf": 1.782,
  "warnings": [],
  "voice": {"name": "voice_01"},
  "params": {"emo_mode": 0, "num_beams": 1}
}
```

> **Windows 中文控制台注意**：Git Bash/cmd 可能把 `-d` 里的中文按 GBK 发送，服务端会报 UTF-8 解码错误。把 JSON 写进 UTF-8 文件，用 `curl --data-binary @body.json`，或改用 Python `requests`。

### 8.4 OpenAI 兼容

```bash
curl -s -X POST http://127.0.0.1:8791/v1/audio/speech \
  -H "Authorization: Bearer my-secret" \
  -H "Content-Type: application/json" \
  -d '{"model":"indextts2","input":"OpenAI 兼容测试。","voice":"voice_01","response_format":"mp3"}' \
  -o speech.mp3
```

字段映射：

| OpenAI 字段 | 行为 |
|---|---|
| `input` | → `text` |
| `voice` | 本地音色名（不是 alloy/echo 等 OpenAI 音色） |
| `instructions` | 未显式给其它情感参数时 → `emo_text`（模式 3） |
| `model` | 忽略 |
| `speed` | 只支持 `1.0`；其它值返回 400 |
| `response_format` | `mp3`（默认）/ `wav` / `flac` / `opus` / `aac` / `pcm` |
| `stream_format` | 只支持 `"audio"` |

非 wav 会用 `ffmpeg` 转码；没有 ffmpeg 时请显式传 `response_format=wav`。

Python OpenAI SDK：

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8791/v1", api_key="my-secret")
with client.audio.speech.with_streaming_response.create(
    model="indextts2",
    voice="voice_01",
    input="你好，这是一段语音。",
    response_format="mp3",
) as response:
    response.stream_to_file("hello.mp3")
```

### 8.5 异步任务

```bash
JOB=$(curl -s -X POST http://127.0.0.1:8791/v1/jobs \
  -H "Authorization: Bearer my-secret" -H "Content-Type: application/json" \
  -d '{"text":"异步任务。","voice":"voice_01","fast":true}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['job_id'])")

curl -s -H "Authorization: Bearer my-secret" \
  http://127.0.0.1:8791/v1/jobs/$JOB
```

`state`：`queued` → `running` → `done` / `failed`。完成后 `result.audio_url` 指向 `/v1/audio/<job_id>`。排队中的任务可以 `DELETE /v1/jobs/<id>`；推理中的任务返回 409。

同步请求超过 `--request-timeout`（默认 1800 秒）会返回 504，并在错误体里带 `job_id`——任务仍在守护进程里继续执行，可以改走 `GET /v1/jobs/<id>` 继续等。

### 8.6 上传音色

```python
import base64, requests

raw = open("examples/emo_sad.wav", "rb").read()
r = requests.post(
    "http://127.0.0.1:8791/v1/voices",
    headers={"Authorization": "Bearer my-secret"},
    json={
        "name": "旁白女声",
        "filename": "emo_sad.wav",
        "audio_base64": base64.b64encode(raw).decode(),
        "notes": "女声，语气平稳",
        "tags": ["旁白"],
    },
)
print(r.status_code, r.json())
```

支持 `wav/mp3/flac/m4a/aac/ogg/opus/webm`，上限 20 MiB；服务端会先尝试真正解码一次（需要 ffmpeg），坏文件不会登记。Docker 需要挂载 `-v /host/voices:/app/voices`。

### 8.7 音频下载与 Range

`GET /v1/audio/<job_id>` 和 `GET /v1/audio-file/<name>` 支持：

- `Range: bytes=0-99` → `206` + `Content-Range`
- `?download=1` → `Content-Disposition: attachment`
- 只提供输出目录内的 wav；路径穿越和 `..` 会被拒绝

### 8.8 错误结构

原生端点：

```json
{
  "ok": false,
  "schema": 1,
  "error": {
    "code": "CUDA_OOM",
    "message": "显存不足（阶段：infer）…",
    "hint": "调小 max_text_tokens_per_segment…",
    "retriable": false
  }
}
```

`/v1/audio/speech` 使用 OpenAI 错误形状。HTTP 状态映射：参数错误 400、音色/任务不存在 404、音色已存在/任务未完成/无法取消 409、请求体过大 413、队列满 429、守护进程不可用/OOM 503、同步等待超时 504。

完整端点与示例见 [`references/rest-api.md`](references/rest-api.md)。

---

## 9. 配置与环境变量

### 9.1 `config.json`

`config.json` 在项目根目录，所有键都可省略（省略即默认值）：

| 键 | 默认 | 说明 |
|---|---|---|
| `project_dir` | `null` | 项目目录；`null` 表示项目自身 |
| `port` | `8788` | 守护进程端口（被占用时向上扫到 8800） |
| `fp16` | `true` | 半精度推理 |
| `idle_timeout` | `3600` | 守护进程空闲多少秒后退出；`0` 表示不退出 |
| `max_pending` | `4` | 队列上限 |
| `startup_timeout` | `300` | 启动等待上限（秒） |
| `use_cuda_kernel` | `false` | 上游可选 CUDA kernel |
| `use_deepspeed` | `false` | 上游可选 DeepSpeed |
| `allow_network` | `false` | 允许 Hugging Face 联网（默认离线） |
| `auto_download` | `true` | 缺失模型时自动下载 |
| `auto_bootstrap` | `true` | 缺失 Python 环境时用 uv 自动建立 |
| `device` | `null` | `auto` / `cuda:0` / `mps` / `cpu` |
| `pypi_index` | `null` | uv 安装依赖时的 PyPI 镜像 |
| `ffmpeg` | `null` | 指定 ffmpeg |
| `python` | `null` | 指定解释器（不可用时直接报错） |
| `default_voice` | `null` | CLI/API/WebUI 的默认音色 |
| `out_dir` | `null` | 输出目录（默认 `runtime/out`） |
| `state_dir` | `null` | 状态目录（默认由 `project_dir` 派生） |

### 9.2 环境变量

| 变量 | 作用 |
|---|---|
| `INDEXTTS2_HOME` | 覆盖 `project_dir` |
| `INDEXTTS2_PYTHON` | 强制指定解释器 |
| `INDEXTTS2_PORT` | 覆盖守护进程端口 |
| `INDEXTTS2_STATE_DIR` | 覆盖状态目录（Docker/多用户必用） |
| `INDEXTTS2_OUT_DIR` | 覆盖默认输出目录 |
| `INDEXTTS2_FFMPEG` | 指定 ffmpeg |
| `INDEXTTS2_DEVICE` | `auto` / `cuda:0` / `mps` / `cpu` |
| `INDEXTTS2_PYPI_INDEX` | uv 安装依赖时的 PyPI 镜像 |
| `INDEXTTS2_WEBUI_PORT` | WebUI 端口（默认 8790） |
| `INDEXTTS2_API_PORT` | API 端口（默认 8791） |
| `INDEXTTS2_API_KEY` | API 鉴权 key |
| `HF_ENDPOINT` | Hugging Face 镜像，如 `https://hf-mirror.com` |
| `PYTORCH_ENABLE_MPS_FALLBACK` | macOS 自动设为 1 |

状态目录（Windows 默认）：

```
%LOCALAPPDATA%\indextts2\<sha1(normcase(project_dir))[:8]>\
├── instance.lock
├── daemon.json
├── daemon.log
└── voices\<sha1>.wav
```

Linux/macOS 在 `$XDG_CACHE_HOME/indextts2/<hash>/`（未设置 `XDG_CACHE_HOME` 时是 `~/.cache/indextts2/<hash>/`）。状态目录由 `project_dir` 派生，保证同一份模型目录只有一个守护进程。

---

## 10. 目录结构

```
index-tts2-cli/
├── scripts/
│   ├── indextts2.py          # CLI 入口
│   ├── indextts2.cmd/.sh     # Windows / POSIX 启动器
│   ├── indextts2-webui.*     # WebUI 启动器
│   ├── indextts2-api.*       # API 启动器
│   └── itts/                 # CLI 实现（conf/spec/reqs/srv/engine/api/webui/...）
├── indextts/                 # IndexTTS2 推理源码
├── checkpoints/              # 模型权重（8.5 GB；不提交到 git，setup 下载/本地放置）
├── venv/                     # 本地 Python 环境（平台绑定；不提交）
├── examples/                 # voice_01…voice_12、emo_sad、emo_hate
├── voices/                   # 命名音色库（本地个人音色不提交；.gitkeep 保留目录）
├── runtime/out/              # 默认输出目录（运行时生成，不提交）
├── references/               # 详细文档
├── pyproject.toml / uv.lock  # 依赖锁定
├── config.json               # 配置
├── Dockerfile
├── LICENSE / LICENSE_ZH.txt  # 上游模型使用许可
├── NOTICE.md                 # 非官方衍生作品声明
├── SKILL.md                  # 面向智能体的使用说明
└── README.md
```

---

## 11. 故障排查

| 症状 | 先做什么 |
|---|---|
| 第一次调用约 30~45 秒 | 正常，模型加载。`daemon.was_cold_start=true`，不要重试 |
| 之后每次仍很慢 | `indextts2 status` 看守护进程是否在跑；检查 `idle_timeout` |
| 音频被截断/不完整 | 检查 `warnings`，调大 `--max-mel-tokens` |
| 显存不足（退出码 6） | 用 `--fast`、调小 `--max-text-tokens-per-segment`、拆短文本，或 `indextts2 stop` 后重试 |
| 模型缺失 | `indextts2 setup`；或 `indextts2 doctor --fetch` |
| 守护进程卡住/坏了 | `indextts2 stop --force` 后重试；看 `daemon.log` |
| WebUI/API 端口被占用（退出码 4） | 换 `--listen-port`；注意 `--port` 是守护进程端口 |
| API 返回 401 | 加 `Authorization: Bearer <key>` 或 `X-API-Key`；key 来自 `--api-key` / `INDEXTTS2_API_KEY` |
| WebUI 上传音色失败（Docker） | `voices/` 是只读镜像层；挂载 `-v /host/voices:/app/voices` |
| WebUI/API 第一次生成很慢 | 守护进程冷启动；等右上角变「就绪」，或启动时加 `--preload` |
| OpenAI 端点报 ffmpeg 缺失 | 传 `response_format=wav`，或安装 ffmpeg（Docker 镜像已内置） |
| 远程访问不到 WebUI/API | 显式加 `--host 0.0.0.0`，并检查防火墙/端口映射 |
| `-p 8788:8788` 访问不到 | 守护进程只监听容器内 127.0.0.1；对外请用 8790/8791 |
| Docker 模型加载很慢 | 不要从 Windows 盘 bind mount `checkpoints/`；先 `docker cp` 到 named volume，或放 Linux 本地盘 |
| WSL 里模型加载 180 秒 | 把 `checkpoints/` 从 `/mnt/` 拷到 `~` 下 |

更多症状见 [`references/troubleshooting.md`](references/troubleshooting.md)。

---

## 12. 文档索引

| 需要什么 | 读哪份 |
|---|---|
| 面向智能体的完整命令与约定 | [`SKILL.md`](SKILL.md) |
| 完整命令面、`say` 全部参数 | [`references/cli.md`](references/cli.md) |
| 情感模式、八维向量、bias 归一化 | [`references/emotion.md`](references/emotion.md) |
| 音色增删改查、自动发现、缓存 | [`references/voices.md`](references/voices.md) |
| JSON 输出字段、错误结构、退出码 | [`references/output-contract.md`](references/output-contract.md) |
| WebUI 功能、上传、端口与安全 | [`references/webui.md`](references/webui.md) |
| 对外 HTTP API、OpenAI 兼容、curl/SDK 示例 | [`references/rest-api.md`](references/rest-api.md) |
| 内部守护进程 HTTP 接口 | [`references/http-api.md`](references/http-api.md) |
| 报错、变慢、显存不足、音频截断 | [`references/troubleshooting.md`](references/troubleshooting.md) |
| 换平台、Linux/macOS/Docker、环境变量 | [`references/deployment.md`](references/deployment.md) |
| 架构、状态目录、实现约束 | [`references/internals.md`](references/internals.md) |

---

## 13. 许可与致谢

- IndexTTS2 由 **Bilibili IndexTTS Team** 开发：<https://github.com/index-tts/index-tts>
- 本项目是**非官方社区封装**，与 Bilibili 无隶属关系。
- 模型权重、上游代码、示例以及本仓库中的衍生作品受 **bilibili Model Use License Agreement** 约束，完整协议见：
  - [`LICENSE`](LICENSE)（英文）
  - [`LICENSE_ZH.txt`](LICENSE_ZH.txt)（中文）
- 协议 4.1(a) 要求的声明：**该衍生品对原模型所作的任何改动与原模型原始权利人无关，原始权利人对该衍生品不背书、不担保、不承担责任。**
- 除非另有说明，本仓库的封装代码也按同一许可证分发；第三方组件仍受其各自许可证约束。见 [`NOTICE.md`](NOTICE.md)。
