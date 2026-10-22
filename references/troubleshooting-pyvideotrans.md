# 故障排查

按报错关键词查找。所有条目均为实测。

## `setup` 失败 / 初始化不成功

**`vendored pyVideoTrans source is missing`**
技能内 `vendor/pyvideotrans` 缺少 `cli.py`。重新安装技能目录，或用 `--copy-from <dir>` 指向一个包含 `cli.py` 的 pyVideoTrans 目录。

**`uv sync` 下载失败 / 卡住**
- 确认 `uv` 在 PATH（或设置 `UV_BIN`）。
- 指定镜像：`setup --index-url https://pypi.tuna.tsinghua.edu.cn/simple`
- 指定代理：`setup --proxy http://127.0.0.1:2080`，或先设置 `HTTPS_PROXY`；脚本也会自动读取 `git config --global http.proxy`。
- PyTorch 轮子来自 `download.pytorch.org`，国内直连通常只有几百 KB/s，走代理会快很多。

**`venv not found after uv sync`**
检查 `vendor\pyvideotrans\pyproject.toml` 和 `.python-version` 是否完整；可 `setup --copy-from <dir>` 重新复制后再试。

**磁盘空间**
`.venv` 约 8.3 GB，模型另算（`large-v3` 约 3 GB）。确保目标盘有 15 GB 以上余量。无 NVIDIA GPU 时用 `--torch cpu` 可省约 3 GB。

**`429 Rate limited`（hf-mirror）**
模型下载时镜像限流。脚本会自动重试；若持续失败会自动回退到 `huggingface.co` + 代理。也可以设置 `HTTPS_PROXY` 后重跑。

**切换 `--torch cpu/cu128` 后依赖不一致**
切换会改写运行时 `pyproject.toml` 并触发 uv 重新解析。切换后请再跑一次不带 `--no-sync` 的 `setup`。

**Ollama 便携包很大**
Windows 版约 1.5 GB，只在需要本地翻译渠道时用 `--with-ollama`。已安装 Ollama 会自动复用，不会重复下载；缺模型时会通过 `/api/pull` 自动拉取。

**离线环境**
加 `--no-download`，缺什么会明确报错。保留 `<skill_dir>\tools\_cache\` 与 `<skill_dir>\vendor\pyvideotrans\models\` 可实现离线重装。

**Windows venv 不能直接拷贝**
venv 内含绝对路径。换机器/换目录时不要复制 `.venv`，重新运行 `setup` 更可靠。

## `[Failed] Task error: <文件> is not exists`

**原因**：输入文件被删了。上游在任务开始时对 `--output-dir` 执行 `shutil.rmtree`（`--clear_cache` 默认开启）。如果把输入放在输出目录里，源文件会先被删掉。

**处理**：
- 输入与输出目录必须分开。
- 包装脚本默认输出到 `<输入名>_translated/` 并会主动拒绝危险组合；若手工调用 `cli.py`，加 `--no-clear-cache` 或换输出目录。

## `Missing credentials. Please pass an api_key, workload_identity, ...`

**原因**：使用本地 LLM / OpenAI 兼容渠道时，`localllm_key` 为空。OpenAI SDK 即使在 Ollama 下也要求非空 Key。

**处理**：

```jsonc
// videotrans/params.json
"localllm_key": "ollama"     // 任意非空字符串
```

包装脚本会自动补这个占位值。

## DeepSeek 报 `Authentication Fails ... api key ****xxxx is invalid`

**先别急着换 Key。** `GET https://api.deepseek.com/models` 对该 Key 可能返回 invalid（假阴性），但对话接口是正常的。

**正确验证方式**：

```bash
python <skill_dir>/scripts/trans_cli.py test-channel --channel deepseek
```

它会实际发一次 `/chat/completions` 请求。只有这个失败才说明 Key 真的无效。

## 控制台中文乱码

Windows 中文控制台默认 GBK。包装脚本已为子进程设置 `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`；若仍乱码，先 `chcp 65001`。日志文件本身是 UTF-8，可用编辑器正常查看。

## 模型下载慢 / 卡住

- `setup --models <name>` 优先**直连 hf-mirror**；失败后自动回退到 `huggingface.co` + 代理。
- 先查缓存：技能内 `vendor\pyvideotrans\models\` 与外部安装的 `models\` 都会命中，命中则直接复制，不联网。
- 运行任务时，应用自身优先 ModelScope（faster-whisper 系列），其他模型走 `huggingface.co`，不通时回落 `hf-mirror.com`。
- 可显式指定：`set HF_ENDPOINT=https://hf-mirror.com`
- 若配置了本地代理，应用会自动读取（例如 `http://127.0.0.1:2080`）；`NO_PROXY` 已包含 `127.0.0.1` / `localhost`，本地服务不会被代理。

## `ffmpeg` 找不到

上游要求 `ffmpeg` / `ffprobe` 在 PATH 中，或把两个 exe 放到项目根目录。

```bash
python <skill_dir>/scripts/trans_cli.py check
```

输出中 `ffmpeg` / `ffprobe` 显示为 `(missing)` 时需要先安装并加入 PATH。

## `uv` 找不到

安装 uv（https://docs.astral.sh/uv/），或设置 `UV_BIN` 指向 `uv.exe`。包装脚本调用 `uv run cli.py`，不会污染系统 Python。

## 任务失败但看不出原因

1. 看包装脚本打印的 `last log` 路径。
2. 日志目录：`<pyvideotrans>\logs\*.log`（包装脚本也会回退查找 `<pyvideotrans>\videotrans\logs\`）
3. 临时目录：`<pyvideotrans>\tmp\<pid>\<uuid>\`（音频、中间字幕、分片）

## GPU 没被使用

```bash
python <skill_dir>/scripts/trans_cli.py check --gpu
```

期望看到 `cuda_available: True` 和显卡型号。若为 False，检查 torch 是否为 `+cu128` 版本、NVIDIA 驱动是否正常。包装脚本默认加 `--cuda`。

## 配音失败 / 音色不存在

```bash
python <skill_dir>/scripts/trans_cli.py voices --language en --locale en-US
```

音色 ID 必须与列表完全一致（区分大小写）。Edge-TTS 还需要能访问微软语音服务。

## `tts` 任务报成功但没有输出文件

**原因**：上游 bug。单行字幕 + Edge-TTS 时，`videotrans/task/dubbing.py:236-239` 的 `align()` 直接返回，从不生成 `target_wav`，但任务仍报告成功。默认 IndexTTS2 不受影响。

**处理**：用 Edge-TTS 时字幕至少两行，或改用非 Edge 的 TTS 渠道。封装脚本检测到单行字幕时会打印警告。

## 翻译结果格式错乱 / 字幕缺失

LLM 渠道可能返回不符合 SRT 格式的内容。处理顺序：

1. 换更强的模型（如 DeepSeek `deepseek-flash`、Ollama 换成更大的模型）。
2. 降低并发（上游默认 `trans_thread=40`，对本地小模型过高）。
3. 重跑该片段：删掉输出目录后重试。
4. 用 `sts` 只重跑字幕翻译，避免重复识别/配音。

## 输出目录被清空

这是上游 `--clear_cache` 的默认行为，不是 bug。包装脚本会用以下策略保护：

- 默认输出到新的 `<输入名>_translated/`，重名时自动加 `-2`、`-3`。
- 显式 `--outdir` 且目录非空时**拒绝执行**，除非加 `--overwrite`（表示你确认可以删）。
- 拒绝把输出目录设为输入所在目录、用户主目录、盘符根目录、pyvideotrans 安装目录，或与技能目录重叠（技能目录本身及其祖先/子目录）。

## 跨平台问题

**macOS / ARM Linux 上 `uv sync` 因 pynini 失败**
`pynini` 只有 Windows 和 Linux x86_64 的预编译轮子。重新运行 `setup` 会自动移除 `pynini` / `WeTextProcessing`（仅影响 MOSS TTS 的文本规范化，主流程不受影响）。若要保留，需自行安装 OpenFst 并加 `--keep-pynini`。

**macOS 找不到 ffmpeg**
`setup` 会在检测到 Homebrew / MacPorts 时自动执行安装；否则手动 `brew install ffmpeg`，再重跑 `setup`。

**macOS 需要 Ollama**
自动下载仅覆盖 Windows/Linux；macOS 请从 https://ollama.com/download 安装，脚本会自动复用。

**Linux 没有 NVIDIA GPU**
`--torch auto` 会选 CPU 轮子；也可显式 `--torch cpu` 省下约 3 GB 下载。

**路径与启动器**
所有路径统一用 `pathlib` 并相对技能目录解析。默认回退安装路径：各平台统一为 `~/pyvideotrans`（仅当技能内 `vendor/pyvideotrans` 不存在时使用）。启动器：仓库根目录的 `trans.cmd`（Windows）与 `./trans`（macOS/Linux）；只安装技能目录时直接调用 `<skill_dir>/scripts/trans_cli.py`。

## 不要跑上游的 pytest

仓库自带的测试套件本身就有一大部分是红的（测试代码与源码脱节，例如引用了已重构掉的符号）。**这与安装/运行无关**，不要用 `pytest` 判断环境是否正常。判断标准是：`check` 通过、`vtv`/`stt` 能产出文件。
