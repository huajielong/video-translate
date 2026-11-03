# video-translate

可迁移的视频翻译 / 克隆配音技能：**ASR → 翻译 → 克隆配音 → 对齐/合成 → 响度归一化 → QC**。

适合单条视频或整批课程翻译。默认英文 → 中文，克隆原说话人音色，输出硬字幕或软字幕。
技能入口和完整规则见 [`SKILL.md`](SKILL.md)。

## 特性

- 翻译与断句审校默认**本地优先**：先探测 llama.cpp / Ollama 等 OpenAI 兼容服务，本地不可用才回退 DeepSeek。
- 翻译前后语义重断句，LLM 审校后生成 TTS 语义单元；长片按 ≤90 条字幕分块审校
  （`resegment_chunk_cues`），不会因单次输出被截断而整条退回规则断句。
- 显示字幕保持一行，上限按视频帧宽动态收紧（16:9 为 22 字，960x1080/640x720 约 14 字）。
- TTS 绝对原速；音频长于槽位时慢放视频片段，短于槽位时最多加快 1.25×，目标停顿 300ms。
- 每个 TTS 单元精确裁边、20ms 淡入淡出、前后 80ms 纯静音，减少句间“嗖”声。
- 逐句配音缓存、断点续跑、QC 汇总。
- **批量课程**：`prep` 多课并行、`dub` 串行（IndexTTS2 单 worker），QC 自动归档，
  支持 `status` 进度与 `clean` 交付后清理。
- **prep 门禁**：`prep_check.py` 在开始耗时的配音前检查字幕齐全性、术语命中率和
  TTS 单元长度；子代理声称“完成”时可用它机械核验。
- Windows 下可用 `pythonw` 无窗口运行，避免 cmd / conhost 闪窗。

> **首次使用**：先确认本地 LLM 或 DeepSeek Key 至少有一个可用（本地优先，见下文
> “本地 LLM”），再确认 `source_lang` / `target_lang`、`subtitle_mode`、`glossary`
> 等关键参数，最后运行 `doctor`。两者都不可用时 `run`/`prep` 会提前退出并说明原因。

## 环境要求

| 项 | 推荐 |
|---|---|
| OS | Windows 11 优先；Linux x86_64 可用但未完整验证；macOS 未验证 |
| GPU | NVIDIA，≥12GB 显存（IndexTTS2 约 7.6GB） |
| RAM / 磁盘 | ≥16GB RAM；≥20GB 空闲磁盘 |
| Python | 3.10+ |
| 工具 | uv、ffmpeg、Rubberband |
| 网络 | 能访问 PyPI / Hugging Face / ModelScope；在线兜底时需访问 DeepSeek API |
| API | 本地 OpenAI 兼容服务（优先）或 DeepSeek Key（兜底，只写入本机 `params.json`） |

模型权重由 `setup` 下载，不随仓库分发。

## 首次使用：自动下载第三方内容

为了保持仓库干净并尊重第三方许可证，本仓库**不包含**：

```text
<vendor 根>/pyvideotrans      # 默认 %LOCALAPPDATA%\video-translate\vendor
<vendor 根>/index-tts2-cli
tools/rubberband
模型权重、checkpoints、venv
config.json / params.json / 任何 API Key
课程视频、字幕、输出目录
```

运行时默认装在**技能目录之外**（Agent 会把整个技能目录镜像复制）；换盘用
`VTV_VENDOR_DIR` 或 `setup --vendor-dir DIR`。

但**首次使用会自动下载并安装**：

1. `setup`（以及缺失时的 `run` / `dub` / `serve` / `prep`）会：
   - clone **pyVideoTrans v4.16**，应用 `patches/pyvideotrans.patch`；
   - clone **IndexTTS2 v2.0.0**，叠加 `wrapper/index-tts2-cli/` 的 CLI wrapper，
     应用 `patches/index-tts.patch`；
   - 调用现有 `setup` 创建 venv、下载 Whisper / IndexTTS2 模型和 checkpoints。
2. 需要本机有 **git**、网络和约 **20GB+** 空闲磁盘。
3. 不想自动下载时设 `VTV_AUTO_INSTALL=0`，改用已有安装：

```bash
python scripts/pipeline.py setup \
  --reuse-pvt /path/to/pyvideotrans \
  --reuse-itt /path/to/index-tts2-cli
```

4. **Rubberband** 是可选的（缺失时退回 pydub/ffmpeg）；Windows 可放到
   `tools/rubberband/` 或 PATH，Linux `apt install rubberband-cli`，macOS
   `brew install rubberband`。

补丁和 wrapper 的版本、许可证见 [`patches/README.md`](patches/README.md)、
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。IndexTTS2 不是 OSI 开源许可证；
商用或大规模使用前请确认上游条款。

## 安装

### Claude Code

```bash
git clone https://github.com/huajielong/video-translate.git \
  ~/.claude/skills/video-translate
cd ~/.claude/skills/video-translate

# 首次运行会自动下载 pyVideoTrans / IndexTTS2 并安装模型
python scripts/pipeline.py setup
python scripts/pipeline.py doctor
```

Windows PowerShell：

```powershell
git clone https://github.com/huajielong/video-translate.git `
  "$env:USERPROFILE\.claude\skills\video-translate"
cd "$env:USERPROFILE\.claude\skills\video-translate"
python scripts\pipeline.py setup
python scripts\pipeline.py doctor
```

也可以把本目录放到项目的 `.claude/skills/video-translate` 作为项目级技能。
如果发布为 Claude Code 插件，再补 `.claude-plugin/plugin.json`；直接目录安装不需要。

### Codex

Codex 读取仓库根目录的 [`AGENTS.md`](AGENTS.md)。克隆到任意目录：

```bash
git clone https://github.com/huajielong/video-translate.git
cd video-translate
```

然后在 Codex 中直接说：

```text
Read AGENTS.md and SKILL.md, run doctor, then translate ...
```

`AGENTS.md` 已经包含命令、vendor 约束和执行规则。

## 快速开始

```bash
# 1. 安装依赖、模型，写本机 config/params
python scripts/pipeline.py setup

# 2. 自检
python scripts/pipeline.py doctor

# 3. 启动 IndexTTS2 API :8791 + Gradio 桥接 :7861（批量时只需启动一次）
python scripts/pipeline.py serve

# 4. 翻译（prep → dub → post；也可以只跑其中一段）
python scripts/pipeline.py run \
  --src "D:/videos" --out "D:/translated" \
  --source en --target zh-cn --backend index

# 可选：只处理部分视频（按文件名前缀匹配）
python scripts/pipeline.py run \
  --src "D:/videos" --out "D:/translated" --videos "001,005"

# 5. 汇总 QC
python scripts/pipeline.py report --out "D:/translated"
```

产物：

```text
<out>/<原名><final_suffix>.mp4      # 成品（final_suffix 可配，如 _中文）
<out>/_字幕/<原名>.{源,目标}.srt     # 字幕，故意与 MP4 分开，避免播放器叠成两层
<out>/QC_SUMMARY.md                 # QC 汇总
<out>/_work/                        # 中间文件：prep 结果、配音轨、中间视频
```

`_work` 用于断点续跑与局部重做；**整批交付并 QC 通过后可以删除**（见“批量课程”的
`clean`）。删除后想再改某条视频，需要重新翻译该条。

Windows 后台 / Agent 场景：

```bash
pythonw scripts/pipeline.pyw run --src "D:/videos" --out "D:/translated"
```

## 批量课程

`scripts/batch_courses.py` 用 manifest 驱动多门课：`prep` 可多课并行，`dub+post`
严格串行（IndexTTS2 单 worker）。清单示例：

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

- `run` 会先 `serve`，逐课串行执行；每课结束把 QC 归档到
  `<qc_dir>/<编号>_<课程名>/`，并写 `batch_status.json`、`batch_summary.md`。
- 中断后重跑同一 `run` 命令即可：已完成且 QC 通过的视频会跳过。
- 重复 stem（多门课都有 `001_Introduction`）靠课程归档区分；历史批次续跑时把
  `qc_dir` 指到原归档目录。
- `status` 每课显示 `finals`（成品）、`dubbed`（已合成）和 `pending`（缺成品或 QC 未过）。
- `clean` 只删除 `batch_status.json` 里 `ok=true`、成品齐全且 QC 全部通过的课程的
  `_work`；其余课程保留并返回非零。先加 `--dry-run` 查看将删除的目录和体积。
- `prep` 与 `run` 分两步跑时，中间可插入 `prep_check.py` 门禁（见下节）。

## 断句审校与质量门禁

- `prep` 先合并源碎句、翻译后按句子重切，再由 LLM 审校生成 TTS 语义单元；
  显示字幕仍是短块，两者块数不同是正常的。
- 审校按 `resegment_chunk_cues`（默认 90 条，`VTV_RESEGMENT_CHUNK_CUES` 可覆盖）
  在句末切块，逐块请求并缓存；长片不会因单次 JSON 输出被截断而整条回退。
- 小模型常把 4 块以上并成一组：`repair_groups` 会机械拆成 ≤3 块单元；只有覆盖
  不连续/漏块才判定失败。本地后端“分组非法”（rc=4）不会让它被标记失效，
  仅网络/连接失败（rc=3）才切换下一个 provider。
- 本地 llama-server 高负载下偶发连接重置（WinError 10054）：审校请求对
  ConnectionReset/BrokenPipe 与 502/503/504 自动重试一次，仍失败才 fallback。
- 翻译缓存命中但缺 `<target>.tts.srt` 时，`prep` 会自动只重跑 LLM 审校，不重新翻译。

`prep` 完成后、`dub` 之前建议跑门禁（也可用于核验子代理的“已完成”声明）：

```bash
python scripts/prep_check.py --manifest courses.json
python scripts/prep_check.py --out "D:/videos/course/_中文译制"
```

它检查每个视频的 `<source>.srt` / `<target>.srt` / `<target>.tts.srt` 是否齐全、
术语 recall 是否 ≥95%、TTS 单元是否超 `resegment_target_max_chars`；任一失败
退出码非零。术语表用 `原文=译文1|译文2` 记录可接受的多种译法，减少误判。

## 本地 LLM（优先）

`translate_channel=auto`（默认）会按顺序探测：

1. `VTV_LOCAL_LLM_API` / `local_llm_api`
2. `local_llm_candidates`：默认 `http://127.0.0.1:8081/v1`（llama.cpp），
   然后 `http://127.0.0.1:11434/v1`（Ollama）
3. `params.json` 的 `localllm_api`

判定方式是 `GET <base>/models`。单模型服务（`llama-server`）直接使用；多模型服务
（Ollama）需用 `local_llm_model` 或 `VTV_LOCAL_LLM_MODEL` 指定模型，否则跳过，
避免误用小模型。命中后 MT、断句审校、`polish` 都走本地。

```jsonc
// config.json
{
  "translate_channel": "auto",
  "local_llm_api": "",                          // 空 = 自动探测
  "local_llm_model": "qwen3.6:35b-a3b-mtp-q4_K_M",
  "local_llm_candidates": "http://127.0.0.1:8081/v1,http://127.0.0.1:11434/v1"
}
```

> 本地 LLM 与 IndexTTS2 会争显存。批量流程：`prep`（本地 LLM 在线）→ 停本地
> 服务 → `run`（MT 命中缓存，只做 TTS）→ 下一门课再启动本地服务。

## DeepSeek API Key（本地不可用时兜底）

只有本地 LLM 全部不可达时才需要。Key **不存在** `config.json`，而是写到 pyVideoTrans 的
`params.json`：

```text
<pyvideotrans_home>/videotrans/params.json
```

默认安装即：

```text
<vendor 根>/pyvideotrans/videotrans/params.json
```

三种配置方式：

```bash
# 1) setup 参数（推荐）
python scripts/pipeline.py setup --deepseek-key "sk-..."

# 2) 环境变量
export DEEPSEEK_API_KEY="sk-..."       # Windows PowerShell: $env:DEEPSEEK_API_KEY="sk-..."
python scripts/pipeline.py setup

# 3) 手动编辑 params.json
cp params.example.json <pyvideotrans_home>/videotrans/params.json
# 然后把 deepseek_key 改成真实 Key
```

`params.example.json` 是模板，`deepseek_key` 留空；不要把真实 Key 写进
`params.example.json` 或提交到 Git。`params.json` 已在 `.gitignore` 中。

`doctor` 只检查 Key 是否存在，不验证有效性；验证渠道用：

```bash
python scripts/trans_cli.py test-channel --channel deepseek
```

或直接跑一条短视频翻译。

## 配置

`config.json` 只放非敏感运行参数，不保存 API Key。`setup` 会自动生成；手动配置可复制模板：

```bash
cp config.example.json config.json
```

常用键：

- `voice_autorate=false`、`video_autorate=true`：声音优先。
- `max_video_speedup_rate=1.25`、`max_phrase_pause_ms=300`。
- `subtitle_mode=hard` 或 `soft`。
- `subtitle_max_chars=22`（CJK 上限；`subtitle_fit_frame=true` 会按帧宽收紧到约 8–22 字）。
- `translate_channel=auto`：本地 LLM 优先，DeepSeek 兜底；`local` 只用本地。
- `resegment_llm_check=true`、`resegment_llm_provider=auto`：LLM 审校断句，本地优先。
- `resegment_chunk_cues=90`：审校分块大小；长片输出截断或本地模型较弱时可调小（如 60）。
- `duration_tol_s=15`：QC 时长容差的绝对下限。
- `glossary=assets/glossary.txt`：换课程/领域时替换术语表。

完整配置见 [`references/configuration.md`](references/configuration.md)。

## 文档索引

| 想做什么 | 读 |
|---|---|
| 查全部命令、参数、`--videos` 过滤 | [`references/cli.md`](references/cli.md) |
| 改配置、环境变量、路径解析 | [`references/configuration.md`](references/configuration.md) |
| 理解阶段、并发、断点续跑 | [`references/pipeline.md`](references/pipeline.md) |
| 调字幕/翻译/克隆/响度/QC | [`references/quality.md`](references/quality.md) |
| 配音节奏、`fit_*` 标定 | [`references/audio-fit.md`](references/audio-fit.md) |
| 报错、爆音、字幕不同步 | [`references/troubleshooting.md`](references/troubleshooting.md) |
| 安装、迁移、Windows 进程模型 | [`references/deployment.md`](references/deployment.md) |

## 已知边界

- 主要开发和验证环境是 Windows 11 + NVIDIA GPU；Linux 可跑但未完整回归，macOS 未验证。
- IndexTTS2 是单 worker，不能并行跑两条配音任务；`prep` 可以并行。
- 双语字幕保持源/目标 1:1 配对，不做单语字幕的一行二次切分。
- 本地 LLM 与 DeepSeek 都不可用时回退规则断句 + `fit_*`；显示字幕仍会按一行切条。
- 删除 `_work` 后，想修改某条视频需要重新翻译（逐句 TTS 缓存在
  `pyvideotrans/tmp/dubbing_cache`，不受影响）。
- 开源仓库不附带课程视频、字幕、模型或 API Key。

## 许可证

本仓库自有代码使用 MIT，见 [`LICENSE`](LICENSE)。第三方依赖不由本仓库分发，
各自遵循其许可证；使用前请阅读 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
IndexTTS2 不是 OSI 开源许可证，商用或有规模限制时请先确认上游条款。
