---
name: video-translate
description: Translates and dubs videos into another language while cloning the original speaker's voice. Use for single videos or batches: ASR → translation → clone TTS → subtitle burn/embed → loudness normalization → QC, resumable and portable. Language-agnostic; defaults en → zh-cn with IndexTTS2.
---

# video-translate

把视频翻译成目标语言并克隆原说话人音色的一条可迁移流水线：
**ASR → 翻译 → 克隆配音 → 对齐/合成 → 响度归一化 → QC**。
配套 pyVideoTrans（识别/翻译/合成）与 IndexTTS2（零样本克隆）；两者需单独安装，
不随仓库分发。

## 硬规则

1. **先 `doctor` 再跑任务；首次先 `setup`。** 缺依赖时按提示补跑 `setup`，不要直接 `run`。
   仓库不附带 `vendor/`，但 `setup` 会自动 clone pyVideoTrans / IndexTTS2、应用
   `patches/` 并下载模型；需要 git、网络和约 20GB+ 磁盘。已有安装可用
   `setup --reuse-pvt ... --reuse-itt ...`，或用 `VTV_AUTO_INSTALL=0` 关闭自动下载。
2. **不要把输入视频放进输出目录。** 上游 `clear_cache` 默认递归删除输出目录；`pipeline.py` 已规避，手工调用 `trans_cli.py` 时必须加 `--no-clear-cache`。
3. **SRT 不要放在成品 MP4 旁边。** 播放器会自动加载同名 SRT，叠加硬字幕形成“两层字幕”；本流水线统一放到 `<out>/_字幕/`。
4. **IndexTTS2 是单 worker。** 不要并发请求同一守护进程；`serve` 启动的桥接会按 `index_restart_every` 自动重启守护进程，不要再手动重启。
5. **优先改配置，不改 vendor。** 换后端/语言/字幕模式用 CLI 参数或 `config.json`；升级上游后本项目补丁需重新应用，见 `references/deployment.md`。
6. **Windows 后台用 `pythonw scripts/pipeline.pyw`，不要调用 `.cmd` 或 venv 的 `pythonw.exe` launcher。**
   两者都会再拉起 `python.exe` / `conhost`，闪黑窗；进程模型与修复见 `references/deployment.md`。

## 最短路径

```bash
python scripts/pipeline.py setup      # 首次：依赖 + 模型 + 写 config/params
python scripts/pipeline.py doctor     # 自检
python scripts/pipeline.py serve      # IndexTTS2 API :8791 + Gradio 桥接 :7861
python scripts/pipeline.py run --src "D:/videos" --out "D:/translated" \
  --source en --target zh-cn --backend index
python scripts/pipeline.py report --out "D:/translated"   # 汇总 QC 到 <out>/QC_SUMMARY.md
```

`vendor/`、模型、checkpoints、`config.json` 和 API Key 都不随仓库分发；
安装与许可证见根目录 `README.md`、`THIRD_PARTY_NOTICES.md`。

产物：`<out>/<原名><final_suffix>.mp4`、`<out>/_字幕/<原名>.{源,目标}.srt`、`<out>/QC_SUMMARY.md`。
中途失败直接重跑同一命令；已完成的阶段和逐句配音会命中缓存。

Windows 后台/Agent 运行用 `pythonw scripts/pipeline.pyw <command>`：解释器没有控制台，
不会闪 cmd 窗口；没有 stdout 时进度写到 `.runtime/logs/entry.log`。

## 通用处理新视频（默认 audio_native）

1. `doctor` → `serve` → `run --src ... --out ...`；失败重跑同一命令，阶段和逐句配音会命中缓存。
2. `prep` 先合并源碎句，翻译后按句重切；`resegment_llm_check=true` 时把整段字幕交给
   DeepSeek 审校，输出 `<target>.tts.srt` 作为 TTS 语义单元；显示字幕另按一行切条。
3. TTS 保持**绝对原速**；音频长于槽位时慢放视频片段，短于槽位时把视频最多加快
   `max_video_speedup_rate`（1.25×）到“音频 + `max_phrase_pause_ms`（300ms）”。
4. 每个 TTS 单元边缘精确裁到首/末非静音，20ms 淡入淡出，前后各加 80ms 纯静音预滚，
   避免句间“嗖”声。
5. 显示字幕默认硬字幕、**每条一行**（CJK 上限 `subtitle_max_chars=22`）；软字幕用
   `--subtitle soft`，硬/软二选一。非中文目标用 `other_len=60`，不要套用 22 字。
6. `post` 统一 loudnorm；QC 按 `max_video_speedup_rate` / `max_video_pts_rate` 计算
   时长上下界，`duration_tol_s`（默认 15s）只是绝对下限，容忍视频变速造成的漂移。
7. 交付前看 `<out>/QC_SUMMARY.md`：QC PASS、每条一行、字幕与音画同步、句间空白约 300ms。
   句间仍有长空白/爆音时按 `references/troubleshooting.md` 的“句间”两节排查。

按视频情况调参：

| 情况 | 做法 |
|---|---|
| 源视频自带字幕 | `sidecar_subs=true`（默认），跳过 ASR |
| 多人/音色变化大 | 保持 `clone_ref_mode=line`；只要音色稳可用 `cluster` |
| 句间空白仍偏长 | 调大 `max_video_speedup_rate` 或调小 `max_phrase_pause_ms` |
| 想保留更多原片停顿 | 调小 `max_video_speedup_rate` 或调大 `max_phrase_pause_ms` |
| 无 DeepSeek / 审校失败 | 自动回退规则断句 + `fit_*`；也可设 `resegment_llm_check=false` |
| 换课程/领域 | 换对应的 `--glossary`（或 `assets/glossary.txt`），否则 QC 术语命中率会按旧词表误判 |
| 换 TTS 后端/音色/语言 | 先试听，再按 `references/audio-fit.md` 重新标定 `fit_pred_a/b` |

## 按需阅读

**只读当前任务需要的那一份，不要预读整个 `references/`。**

| 你现在要做什么 | 读 |
|---|---|
| 查全部命令、参数、`--videos` 过滤、退出码 | `references/cli.md` |
| 改 `config.json`、环境变量、路径解析 | `references/configuration.md` |
| 理解阶段、并发、断点续跑、dub-only | `references/pipeline.md` |
| 调字幕/翻译/克隆/响度/QC 质量 | `references/quality.md` |
| 配音忽快忽慢、短句被加速、句间停顿、`fit_*` 标定 | `references/audio-fit.md` |
| 句间长空白、句间“嗖”声/爆音、字幕与音画不同步 | `references/troubleshooting.md` |
| 首次安装、迁移新机器、端口与安全 | `references/deployment.md` |
| 报错、两层字幕、CUDA_FATAL、QC 时长失败 | `references/troubleshooting.md` |
| Windows 弹黑窗、conhost、`.cmd`、进程模型 | `references/deployment.md` |
| `fit_merge` 合并 0 次、块数暴增、聚类失败 | `references/audio-fit.md` |
| GPU 锁频、TTS 越跑越慢、CPU 降频、电池 | `references/performance.md` |
| 换翻译/TTS 渠道、查渠道编号 | `references/channels.md` |
| 换语言、查默认音色 | `references/languages.md` |
| 选 ASR 模型 | `references/models.md` |
| 包装脚本自身的坑（clear_cache、Key 验证） | `references/troubleshooting-pyvideotrans.md` |

脚本是**执行**入口，不需要整段阅读：`python scripts/pipeline.py <cmd> --help`。

IndexTTS2 服务本身的 API/音色/情感文档在 `vendor/index-tts2-cli/references/`，只在调试 TTS 服务时按需读。

## 默认值

`index` 克隆后端、硬字幕（每条显示一行，22 字上限）、`en → zh-cn`、`mt_workers=4`、
AAC 48 kHz、-18 LUFS、术语表 `assets/glossary.txt`。软字幕用 `--subtitle soft`。
语言不写死：`--source` / `--target` / `--glossary` 均可配置。
视频旁有 `.en-us.vtt` / `.en.vtt` / `.en.srt` 时直接导入，跳过 ASR（`sidecar_subs=true`）。
默认 **audio_native（声音优先）**：翻译前后 `resegment` 重断句，DeepSeek 审校后把
完整句作为 TTS 语义单元（显示字幕仍是短块）；`voice_autorate=false` 保持 TTS 原速，
`video_autorate=true` 用视频片段 PTS 吸收长度差：音频短时最多加快 1.25× 并保留
`max_phrase_pause_ms=300`，QC 时长上下界由视频变速上限决定、`duration_tol_s=15` 是绝对下限。`fit_merge`/`fit_condense`
只在没有 TTS 语义单元时兜底。细节见 `references/audio-fit.md`，配置见 `configuration.md`。
Windows 上 API/shim/daemon 统一用 base `pythonw`，venv 的 `site-packages` 走
`PYTHONPATH`；聚类失败会让 `fit_merge` 0 合并、块数暴增。

