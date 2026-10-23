# video-translate

可迁移的视频翻译 / 克隆配音技能：**ASR → 翻译 → 克隆配音 → 对齐/合成 → 响度归一化 → QC**。

适合单条视频或批量课程翻译。默认英文 → 中文，克隆原说话人音色，输出硬字幕或软字幕。
技能入口和完整规则见 [`SKILL.md`](SKILL.md)。

## 特性

- 翻译前后语义重断句，DeepSeek 审校后生成 TTS 语义单元；显示字幕保持一行 22 字。
- TTS 绝对原速；音频长于槽位时慢放视频片段，短于槽位时最多加快 1.25×，目标停顿 300ms。
- 每个 TTS 单元精确裁边、20ms 淡入淡出、前后 80ms 纯静音，减少句间“嗖”声。
- 逐句配音缓存、断点续跑、QC 汇总。
- Windows 下可用 `pythonw` 无窗口运行，避免 cmd / conhost 闪窗。

## 环境要求

| 项 | 推荐 |
|---|---|
| OS | Windows 11 优先；Linux x86_64 可用但未完整验证；macOS 未验证 |
| GPU | NVIDIA，≥12GB 显存（IndexTTS2 约 7.6GB） |
| RAM / 磁盘 | ≥16GB RAM；≥20GB 空闲磁盘 |
| Python | 3.10+ |
| 工具 | uv、ffmpeg、Rubberband |
| 网络 | 能访问 PyPI / Hugging Face / ModelScope；DeepSeek API |
| API | DeepSeek API Key（只写入本机 `params.json`） |

模型权重由 `setup` 下载，不随仓库分发。

## 首次使用：自动下载第三方内容

为了保持仓库干净并尊重第三方许可证，本仓库**不包含**：

```text
vendor/pyvideotrans
vendor/index-tts2-cli
tools/rubberband
模型权重、checkpoints、venv
config.json / params.json / 任何 API Key
课程视频、字幕、输出目录
```

但**首次使用会自动下载并安装**：

1. `setup`（以及缺失时的 `run` / `dub` / `serve` / `prep`）会：
   - clone **pyVideoTrans v4.16**，应用 `patches/pyvideotrans.patch`；
   - clone **IndexTTS2 v2.0.0**，叠加 `vendor_src/index-tts2-cli/` 的 CLI wrapper，
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
git clone https://github.com/<you>/video-translate.git \
  ~/.claude/skills/video-translate
cd ~/.claude/skills/video-translate

# 首次运行会自动下载 pyVideoTrans / IndexTTS2 并安装模型
python scripts/pipeline.py setup
python scripts/pipeline.py doctor
```

Windows PowerShell：

```powershell
git clone https://github.com/<you>/video-translate.git `
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
git clone https://github.com/<you>/video-translate.git
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

# 3. 启动 IndexTTS2 API :8791 + Gradio 桥接 :7861
python scripts/pipeline.py serve

# 4. 翻译
python scripts/pipeline.py run \
  --src "D:/videos" --out "D:/translated" \
  --source en --target zh-cn --backend index

# 5. 汇总 QC
python scripts/pipeline.py report --out "D:/translated"
```

产物：

```text
<out>/<原名><final_suffix>.mp4
<out>/_字幕/<原名>.{源,目标}.srt
<out>/QC_SUMMARY.md
```

Windows 后台 / Agent 场景：

```bash
pythonw scripts/pipeline.pyw run --src "D:/videos" --out "D:/translated"
```

## 配置

复制模板后按机器修改：

```bash
cp config.example.json config.json
```

常用键：

- `voice_autorate=false`、`video_autorate=true`：声音优先。
- `max_video_speedup_rate=1.25`、`max_phrase_pause_ms=300`。
- `subtitle_mode=hard` 或 `soft`。
- `subtitle_max_chars=22`（CJK）。
- `resegment_llm_check=true`：DeepSeek 审校断句。
- `duration_tol_s=15`：QC 时长容差的绝对下限。

完整配置见 [`references/configuration.md`](references/configuration.md)。

## 已知边界

- 主要开发和验证环境是 Windows 11 + NVIDIA GPU；Linux 可跑但未完整回归，macOS 未验证。
- IndexTTS2 是单 worker，不能并行跑两条配音任务。
- 双语字幕保持源/目标 1:1 配对，不做单语字幕的一行二次切分。
- DeepSeek 不可用时回退规则断句 + fit；显示字幕仍会按一行切条。
- 开源仓库不附带课程视频、字幕、模型或 API Key。

## 许可证

本仓库自有代码使用 MIT，见 [`LICENSE`](LICENSE)。第三方依赖不由本仓库分发，
各自遵循其许可证；使用前请阅读 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
IndexTTS2 不是 OSI 开源许可证，商用或有规模限制时请先确认上游条款。
