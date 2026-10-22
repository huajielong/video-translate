---
name: video-translate
description: Translates and dubs videos into another language while cloning the original speaker's voice. Use for single videos or batches: ASR → translation → clone TTS → subtitle burn/embed → loudness normalization → QC, resumable and portable. Language-agnostic; defaults en → zh-cn with IndexTTS2.
---

# video-translate

把视频翻译成目标语言并克隆原说话人音色的一条可迁移流水线：
**ASR → 翻译 → 克隆配音 → 对齐/合成 → 响度归一化 → QC**。
内置 pyVideoTrans（识别/翻译/合成）与 IndexTTS2（零样本克隆）。

## 硬规则

1. **先 `doctor` 再跑任务；首次先 `setup`。** 缺依赖时按提示补跑 `setup`，不要直接 `run`。
2. **不要把输入视频放进输出目录。** 上游 `clear_cache` 默认递归删除输出目录；`pipeline.py` 已规避，手工调用 `trans_cli.py` 时必须加 `--no-clear-cache`。
3. **SRT 不要放在成品 MP4 旁边。** 播放器会自动加载同名 SRT，叠加硬字幕形成“两层字幕”；本流水线统一放到 `<out>/_字幕/`。
4. **IndexTTS2 是单 worker。** 不要并发请求同一守护进程；`serve` 启动的桥接会按 `index_restart_every` 自动重启守护进程，不要再手动重启。
5. **优先改配置，不改 vendor。** 换后端/语言/字幕模式用 CLI 参数或 `config.json`；升级上游后本项目补丁需重新应用，见 `references/deployment.md`。

## 最短路径

```bash
python scripts/pipeline.py setup      # 首次：依赖 + 模型 + 写 config/params
python scripts/pipeline.py doctor     # 自检
python scripts/pipeline.py serve      # IndexTTS2 API :8791 + Gradio 桥接 :7861
python scripts/pipeline.py run --src "D:/videos" --out "D:/translated" \
  --source en --target zh-cn --backend index
python scripts/pipeline.py report --out "D:/translated"   # 汇总 QC 到 <out>/QC_SUMMARY.md
```

产物：`<out>/<原名><final_suffix>.mp4`、`<out>/_字幕/<原名>.{源,目标}.srt`、`<out>/QC_SUMMARY.md`。
中途失败直接重跑同一命令；已完成的阶段和逐句配音会命中缓存。

## 按需阅读

**只读当前任务需要的那一份，不要预读整个 `references/`。**

| 你现在要做什么 | 读 |
|---|---|
| 查全部命令、参数、`--videos` 过滤、退出码 | `references/cli.md` |
| 改 `config.json`、环境变量、路径解析 | `references/configuration.md` |
| 理解阶段、并发、断点续跑、dub-only | `references/pipeline.md` |
| 调字幕/翻译/克隆/响度/QC 质量 | `references/quality.md` |
| 配音忽快忽慢、短句被加速、句间停顿、`fit_*` 标定 | `references/audio-fit.md` |
| 首次安装、迁移新机器、端口与安全 | `references/deployment.md` |
| 报错、变慢、两层字幕、CUDA_FATAL | `references/troubleshooting.md` |
| 换翻译/TTS 渠道、查渠道编号 | `references/channels.md` |
| 换语言、查默认音色 | `references/languages.md` |
| 选 ASR 模型 | `references/models.md` |
| 包装脚本自身的坑（clear_cache、Key 验证） | `references/troubleshooting-pyvideotrans.md` |

脚本是**执行**入口，不需要整段阅读：`python scripts/pipeline.py <cmd> --help`。

IndexTTS2 服务本身的 API/音色/情感文档在 `vendor/index-tts2-cli/references/`，只在调试 TTS 服务时按需读。

## 默认值

`index` 克隆后端、硬字幕、`en → zh-cn`、`mt_workers=4`、AAC 48 kHz、-18 LUFS、
术语表 `assets/glossary.txt`。语言不写死：`--source` / `--target` / `--glossary` 均可配置。
视频旁有 `.en-us.vtt` / `.en.vtt` / `.en.srt` 时直接导入，跳过 ASR（`sidecar_subs=true`）。
默认是 **quality_merge** 质量预设：`clone_ref_mode=line`（逐句参考保留情感，
聚类只做同说话人合并）+ beam 3 + 扩散 25 步 + 放慢上限 1.05 + 自然停顿 600ms。
`fit_merge` + `fit_condense` 先合并同说话人短句，再压缩仍超速的句子；
002 实测最大加速 2.75×→1.44×、>1.5× 由 21 句降到 0。质量预设比 beam1/15
快配置慢约 17%。详见 `references/audio-fit.md`；换后端/音色后用
`scripts/fit_calibrate.py` 重标定。

## 通用能力（已内置）

| 能力 | 入口 | 说明 |
|---|---|---|
| 克隆配音 | `--backend index` | IndexTTS2 零样本克隆；默认 `line` 逐句参考，聚类只做同说话人合并 |
| 配音自然度 | `fit_merge` / `fit_condense` | 合并同说话人短句 + 压缩仍超速句；见 `audio-fit.md` |
| 句间停顿 | `max_audio_slow_rate` / `dubb_natural_pause_ms` | 保留自然停顿，只填长空档；默认 1.05/600ms |
| TTS 稳定性 | `scripts/gpu_lock_task.cmd` / `scripts/tts_stability.py` | GPU 锁频、probe、逐句稳定性基准 |
| 断点续跑 | `prep` / `dub` / `post` / `status` | 阶段状态 + 逐行 `DUBBING_CACHE` |
| 质检 | `qc` / `report` | 时长、响度、字幕行数、术语命中 |
| 标定 | `scripts/fit_calibrate.py` | 换后端/音色后重拟合配音时长模型 |

## 本机注意（RTX 5070 Ti Laptop）

GPU 跑到 ~2700MHz 会挤占 CPU，GPT 从 3.6s/句退化到 15–19s/句。
先用**管理员**运行一次 `scripts\gpu_lock_task.cmd`，把 GPU 上限锁到 1200MHz；
本机实测 002 全片 232 句 **5.79s/句、24m39s 完成**（历史慢态 69m28s）。
撤销：`scripts\gpu_lock_task.cmd remove`。
详见 `references/troubleshooting.md`。
