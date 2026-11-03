<div align="center">

# video-translate

**一条命令，把英文课程视频变成中文配音成片**

克隆原说话人音色 · 逐句语义重断句 · 音画自动对齐 · 失败可断点续跑

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey.svg)](#环境要求)

</div>

---

## 效果演示

同一时刻的原始画面与译制画面并排对照：右侧为中文配音（克隆原说话人音色）并烧录一行式中文字幕。

[![翻译前后对比](docs/demo/before-after.gif)](https://huajielong.github.io/video-translate/demo/before-after.mp4)

点击动图直接播放带音频的对比片段（16 秒：前 8 秒英文原声，后 8 秒中文配音）；也可以打开[播放页](https://huajielong.github.io/video-translate/demo/)。

> 示例截取自课程《面向 AI Agent 与应用的语音技术》Introduction，仅用于展示译制效果。

---

## 为什么需要它

把一门英文课程翻译成中文并不难，难的是**译得准、听着像人、音画对得上、几十条视频能一次跑完**。常见做法要么音色机械，要么字幕和语音各说各的，要么跑到第 30 条崩了从头再来。

video-translate 把这些环节串成一条可重入的流水线：

| 你遇到的痛点 | 这里的解法 |
|---|---|
| 机器配音音色全变，"一听就是 AI" | IndexTTS2 零样本克隆**原说话人音色**，逐句用原片作参考 |
| 翻译后句子长短不一，字幕和语音对不上 | 翻译前后各做一次**语义重断句**；TTS 按完整句合成，显示字幕独立切条 |
| 配音语速忽快忽慢，句间大段空白 | TTS **绝对原速**；音频长则慢放视频片段，短则最多 1.25× 追平，句间保留 300ms |
| 长视频跑一半失败，只能全部重来 | 阶段级 + 逐句配音缓存，**重跑同一命令即续跑** |
| 几十上百条视频只能人工排期盯守 | manifest 批量：`prep` 多课并行、配音严格串行、QC 自动归档 |
| 交付前只能靠人耳逐条听 | QC 自动核对时长、字幕行宽、术语命中与音画同步，输出汇总报告 |

## 核心能力

- **翻译与审校本地优先**：自动探测 llama.cpp / Ollama 等 OpenAI 兼容服务，本地不可用才回退 DeepSeek；长片按 ≤90 条字幕分块审校，不会被单次输出截断打回规则断句。
- **一行式显示字幕**：每条字幕保持一行，上限随视频帧宽自动收紧（16:9 为 22 字，960×1080 约 14 字，竖屏约 8 字）。
- **精确的句间节奏**：每个 TTS 单元精确裁边、20ms 淡入淡出、前后各 80ms 纯静音，减少句间"嗖"声与句内"咔哒"。
- **可迁移、可恢复**：逐句配音缓存 + 分阶段执行，换机器、换目录、中断后都能接着跑。
- **批量课程工作流**：`prep` 并行、`run` 串行（IndexTTS2 单 worker），QC 自动归档，`status` 看进度，`clean` 交付后清理中间文件。
- **prep 门禁**：开始耗时的配音前，机械校验字幕齐全性、术语命中率和 TTS 单元长度——子代理声称"完成"时可用它核验。
- **Windows 友好**：`pythonw` 无窗口运行，避免 cmd / conhost 闪窗。

## 工作原理

```text
输入视频（可带 sidecar 字幕）
   │
   ├─ ASR ───────────── 无字幕时用 Whisper large-v3-turbo 识别
   ├─ 语义重断句 ────── 合并碎句、翻译、按句子重切
   ├─ 断句审校 ──────── LLM 输出 <target>.tts.srt 作为 TTS 语义单元
   ├─ 克隆配音 ──────── IndexTTS2 逐句克隆原说话人音色
   ├─ 对齐 / 合成 ───── 视频 PTS 变速吸收长度差，句间 300ms 停顿
   ├─ 响度归一化 ────── loudnorm 统一到 -18 LUFS
   └─ QC ────────────── 时长 / 字幕 / 术语 / 同步 → QC_SUMMARY.md
```

显示字幕与 TTS 单元是两套产物：**TTS 按完整句合成**（自然、少断点），**显示字幕按一行切条**（易读）。二者块数不同属正常现象。

## 快速开始

### 1. 安装

```bash
git clone https://github.com/huajielong/video-translate.git \
  ~/.claude/skills/video-translate
cd ~/.claude/skills/video-translate
```

Windows PowerShell：

```powershell
git clone https://github.com/huajielong/video-translate.git `
  "$env:USERPROFILE\.claude\skills\video-translate"
cd "$env:USERPROFILE\.claude\skills\video-translate"
```

也可以把本目录放到项目的 `.claude/skills/video-translate` 作为项目级技能；仓库内的 `install.sh` / `install.ps1` 提供不带 `.git`、`vendor` 的安装副本。

其他 Agent（如 Codex）读取仓库根目录的 [`AGENTS.md`](AGENTS.md)，其中已包含命令、约束与执行规则。

### 2. 初始化并自检

```bash
python scripts/pipeline.py setup    # 首次：拉取运行时、下载模型、生成本机配置
python scripts/pipeline.py doctor   # 检查 GPU、ffmpeg、模型、后端与密钥
```

`setup` 会自动安装 **pyVideoTrans v4.16** 与 **IndexTTS2 v2.0.0** 到**技能目录之外**的 `<vendor 根>`（Windows 默认 `%LOCALAPPDATA%\video-translate\vendor`），并应用补丁。需要 git、网络和约 20GB+ 空闲磁盘。

换盘用 `VTV_VENDOR_DIR` 或 `setup --vendor-dir DIR`；已有安装可复用；不想自动下载时设 `VTV_AUTO_INSTALL=0`：

```bash
python scripts/pipeline.py setup --reuse-pvt /path/to/pyvideotrans --reuse-itt /path/to/index-tts2-cli
```

### 3. 启动配音服务

```bash
python scripts/pipeline.py serve    # IndexTTS2 API :8791 + Gradio 桥接 :7861
```

批量处理时只需启动一次。

### 4. 翻译

```bash
python scripts/pipeline.py run \
  --src "D:/videos" --out "D:/translated" \
  --source en --target zh-cn --backend index
```

也可以拆成三段按需执行：`prep`（识别/翻译/审校）→ `dub`（配音）→ `post`（合成/响度）。

### 5. 查看 QC

```bash
python scripts/pipeline.py report --out "D:/translated"
```

打开 `<out>/QC_SUMMARY.md` 逐条确认；全部 PASS 即可交付。

## 输出产物

```text
<out>/
├── <原名><final_suffix>.mp4        # 成品（final_suffix 可配，如 _中文）
├── _字幕/
│   └── <原名>.{源,目标}.srt        # 双语字幕，故意与 MP4 分开，避免播放器叠成两层
├── QC_SUMMARY.md                   # QC 汇总
└── _work/                          # 中间文件：prep 结果、配音轨、中间视频（用于续跑）
```

`_work` 用完即可删：整批交付并 QC 通过后执行 `clean`（见下节）。删除后想改单条视频需要重新翻译该条；逐句配音缓存位于 `pyvideotrans/tmp/dubbing_cache`，不受影响。

## 批量译制整门课程

`scripts/batch_courses.py` 用 manifest 驱动多门课程。`prep` 可多课并行，`dub + post` 严格串行（IndexTTS2 单 worker）。

```jsonc
{
  "source": "en",
  "target": "zh-cn",
  "backend": "index",
  "state_dir": "D:/work/_batch",
  "qc_dir": "D:/work/_batch/qc",
  "courses": [
    {"name": "course-a", "root": "D:/videos", "modules": ["M1_Module 1"]},
    {"name": "course-b", "root": "D:/videos",
     "modules": ["M1_Module 1", "M2_Module 2"],
     "out": "D:/videos/course-b/_中文译制"}
  ]
}
```

```bash
python scripts/batch_courses.py prep   --manifest courses.json --workers 4
python scripts/batch_courses.py run    --manifest courses.json [--only 1,3]
python scripts/batch_courses.py status --manifest courses.json
python scripts/batch_courses.py clean  --manifest courses.json --dry-run
python scripts/batch_courses.py clean  --manifest courses.json
```

- `run` 会先 `serve`，逐课串行执行；每课结束把 QC 归档到 `<qc_dir>/<编号>_<课程名>/`，并写 `batch_status.json`、`batch_summary.md`。
- 中断后重跑同一 `run` 命令即可：已完成且 QC 通过的视频自动跳过。
- `status` 每课显示 `finals`（成品数）、`dubbed`（已合成数）与 `pending`（缺成品或 QC 未过）。
- `clean` 只删除 `batch_status.json` 中 `ok=true`、成品齐全且 QC 全部通过的课程的 `_work`；未交付的课程保留并返回非零退出码。先加 `--dry-run` 查看将删除的目录与体积。
- 多门课存在同名文件（如 `001_Introduction`）时靠课程归档目录区分；历史批次续跑请把 `qc_dir` 指回原归档目录。

## 质量门禁

`prep` 完成、`dub` 开始之前，建议跑一次门禁（也可用于核验子代理的"已完成"声明）：

```bash
python scripts/prep_check.py --manifest courses.json
python scripts/prep_check.py --out "D:/videos/course/_中文译制"
```

检查项：`<source>.srt` / `<target>.srt` / `<target>.tts.srt` 是否齐全、术语 recall 是否 ≥95%、TTS 单元是否超过 `resegment_target_max_chars`。任一失败退出码非零。

门禁之外的运行时保障：

- 审校按 `resegment_chunk_cues`（默认 90 条）在句末切块，逐块请求并缓存；长片不会因单次 JSON 输出截断而整条回退。
- 本地小模型常把 4 块以上并成一组：`repair_groups` 会机械拆成 ≤3 块单元，只有覆盖不连续或漏块才判定失败。
- 本地 llama-server 高负载下偶发连接重置（WinError 10054）：审校请求对 ConnectionReset / BrokenPipe 与 502/503/504 自动重试一次。
- 翻译缓存命中但缺 `<target>.tts.srt` 时，`prep` 自动只重跑 LLM 审校，不重新翻译。

## 配置与密钥

配置只放非敏感运行参数：复制 `config.example.json` 为 `config.json` 后按需修改。

```jsonc
{
  "translate_channel": "auto",                 // 本地优先，DeepSeek 兜底
  "local_llm_model": "qwen3.6:35b-a3b-mtp-q4_K_M",
  "local_llm_candidates": "http://127.0.0.1:8081/v1,http://127.0.0.1:11434/v1",
  "resegment_chunk_cues": 90,                  // 长片输出截断时可调小至 60
  "glossary": "assets/glossary.txt"            // 换课程/领域必须替换
}
```

`translate_channel=auto` 会按 `local_llm_api` → `local_llm_candidates`（llama.cpp `:8081`、Ollama `:11434`）→ `params.json:localllm_api` 的顺序探测。单模型服务（llama.cpp）直接使用；多模型服务（Ollama）必须用 `local_llm_model` / `VTV_LOCAL_LLM_MODEL` 指定模型，否则跳过，避免误选小模型。

**DeepSeek 只在本地全部不可达时才需要。** Key 不写入 `config.json`，只保存在：

```text
<pyvideotrans_home>/videotrans/params.json
```

推荐让 `setup` 写入，不要提交到 Git、也不要贴进聊天：

```bash
python scripts/pipeline.py setup --deepseek-key "sk-..."
```

`doctor` 只检查 Key 是否存在，不验证有效性；`python scripts/trans_cli.py test-channel --channel deepseek` 可验证渠道。

```text
本地 LLM 与 IndexTTS2 会争抢显存。批量流程推荐：
prep（本地 LLM 在线）→ 停本地服务 → run（MT 命中缓存，只做 TTS）→ 下一门课再启动本地服务。
```

## 注意事项

1. **先 `doctor` 再跑任务；首次先 `setup`。** 缺依赖时按提示补跑，不要直接 `run`。
2. **不要把输入视频放进输出目录。** 上游 `clear_cache` 默认递归删除输出目录；`pipeline.py` 已规避，手工调用 `trans_cli.py` 时必须加 `--no-clear-cache`。
3. **SRT 不要放在成品 MP4 旁边。** 播放器会自动加载同名 SRT，与硬字幕叠加成"两层字幕"；本流水线统一放到 `<out>/_字幕/`。
4. **IndexTTS2 是单 worker。** 不要并发请求同一守护进程；批量时 `prep` 可并行，`run` 必须按课程串行。
5. **优先改配置，不改 vendor。** 换后端、语言、字幕模式用 CLI 参数或 `config.json`；升级上游后本项目补丁需重新应用。
6. **Windows 后台用 `pythonw scripts/pipeline.pyw`**，不要调用 `.cmd` 或 venv 的 `pythonw.exe`，否则会再拉起 `python.exe` / `conhost` 并闪窗。

## 文档索引

| 你想做什么 | 读这里 |
|---|---|
| 查全部命令、参数、`--videos` 过滤、退出码 | [`references/cli.md`](references/cli.md) |
| 改配置、环境变量、路径解析 | [`references/configuration.md`](references/configuration.md) |
| 理解阶段、并发、断点续跑、dub-only | [`references/pipeline.md`](references/pipeline.md) |
| 调字幕 / 翻译 / 克隆 / 响度 / QC | [`references/quality.md`](references/quality.md) |
| 配音节奏、`fit_*` 标定 | [`references/audio-fit.md`](references/audio-fit.md) |
| 报错、爆音、字幕不同步 | [`references/troubleshooting.md`](references/troubleshooting.md) |
| 安装、迁移、Windows 进程模型 | [`references/deployment.md`](references/deployment.md) |
| GPU 锁频、性能优化 | [`references/performance.md`](references/performance.md) |

脚本是执行入口，无需整段阅读：`python scripts/pipeline.py <command> --help`。

## 环境要求

| 项 | 推荐 |
|---|---|
| OS | Windows 11 优先；Linux x86_64 可用但未完整回归；macOS 未验证 |
| GPU | NVIDIA，≥12GB 显存（IndexTTS2 约 7.6GB） |
| RAM / 磁盘 | ≥16GB 内存；≥20GB 空闲磁盘 |
| Python | 3.10+ |
| 工具 | uv、ffmpeg、Rubberband（可选） |
| 网络 | 可访问 PyPI / Hugging Face / ModelScope；在线兜底时需访问 DeepSeek API |

## 已知边界

- 主要开发与验证环境为 Windows 11 + NVIDIA GPU；Linux 可跑但未完整回归，macOS 未验证。
- IndexTTS2 单 worker，不能并行跑两条配音任务；`prep` 可以并行。
- 双语字幕保持源 / 目标 1:1 配对，不做单语字幕的一行二次切分。
- 本地 LLM 与 DeepSeek 都不可用时回退规则断句 + `fit_*`；显示字幕仍会按一行切条。
- 开源仓库不附带课程视频、字幕、模型或 API Key。

## 许可证

本仓库自有代码使用 MIT，见 [`LICENSE`](LICENSE)。第三方依赖不由本仓库分发，各自遵循其许可证；使用前请阅读 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。IndexTTS2 不是 OSI 开源许可证，商用或有规模限制时请先确认上游条款。
