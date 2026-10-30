# 命令与参数

入口：`python scripts/pipeline.py <command>`。所有参数都可用 `--help` 查看；本页只记录语义与易错点。

Windows 上从 Agent、编辑器或计划任务（父进程没有控制台）运行时，用
`pythonw scripts/pipeline.pyw <command>`：`pythonw` 没有控制台，不会创建/闪烁 cmd 窗口；
没有 stdout 时进度与 traceback 写到 `.runtime/logs/entry.log`。从自己的终端运行仍可用
`python scripts/pipeline.py`，输出直接显示在终端。

## 命令

| 命令 | 作用 | 必需的参数 | 说明 |
|---|---|---|---|
| `setup` | 安装依赖/模型，写 `config.json` 与 `params.json` | — | `--reuse-pvt` / `--reuse-itt` 复用已有安装 |
| `doctor` | 检查 uv/ffmpeg/rubberband/venv/模型/服务/Key | — | 返回非 0 表示有硬性缺失 |
| `serve` | 启动 IndexTTS2 API（8791）+ Gradio 桥接（7861） | — | `--stop` 停止；已在运行则幂等返回 |
| `prep` | ASR + 翻译 | `--src` `--out` | ASR 串行，翻译按 `--mt-workers` 并行 |
| `dub` | 配音 + 对齐 + 合成 | `--src` `--out` | 需要 `prep` 产出的 SRT；会自动启动服务 |
| `post` | loudnorm、字幕归档、QC | `--src` `--out` | 每跑一次都会重新归一化 |
| `run` | `prep → dub → post` | `--src` `--out` | 单条失败不中断批次，最后返回 1 |
| `status` | 每个视频的阶段状态 | `--src` `--out` | 输出 `asr/mt/dub/final/qc` 0/1 |
| `qc` | 单文件 QC | `--video` `--zh` | `--source-video` 默认同 `--video` |
| `polish` | 翻译后轻量审校（只改文本） | `--zh`（或 `--video`） | 输出默认 `<输入>.polished.srt` |
| `report` | 汇总 `.runtime/logs/*.qc.json` | `--out`（或配置） | 输出 `<out>/QC_SUMMARY.md` |
| `tts_stability` | 逐句 TTS 稳定性基准（`scripts/tts_stability.py`） | `--stem` `--src` `--out` | 直连 `/v1/tts`，采样 CPU/GPU/probe，输出到 `.runtime/stability/` |
| `fit_calibrate` | 标定配音时长模型（`scripts/fit_calibrate.py`） | `--probe` `--pid` `--last` | 从 `tts_probe.jsonl` 拟合 `fit_pred_a/b`；换后端/音色后重跑 |

## 批量课程（`scripts/batch_courses.py`）

清单驱动的多课程批处理，prep 可并行、dub+post 必须串行：

```bash
python scripts/batch_courses.py prep   --manifest courses.json --workers 4
python scripts/batch_courses.py run    --manifest courses.json [--only 1,3] [--keep-serve]
python scripts/batch_courses.py status --manifest courses.json
```

manifest 每门课给 `name`、`root`、`modules`（可选 `out`）；顶层可给
`source` / `target` / `backend` / `state_dir` / `qc_dir`。`run` 会先 `serve`，
逐课串行 `run`，每课结束把 QC 归档到 `<qc_dir>/<编号>_<课程名>/`（默认
`<state_dir>/qc`），并写 `batch_status.json`、`batch_summary.md`。中断后重跑
同一 `run` 命令即可：已完成且 QC 通过的视频会跳过。重复 stem（如多门课都有
`001_Introduction`）靠课程归档区分，历史批次续跑时把 `qc_dir` 指到原归档目录。
归档是**合并写入**（保留已有文件），重跑/续跑不会清空先前归档的 QC；否则共享的
`.runtime/logs/<stem>.qc.json` 已被其他课程的同名视频覆盖，会导致归档缺项。

## 共享参数

以下参数由 `prep/dub/post/run/status/qc/polish/report` 共同接受，但只有相关命令会使用：

| 参数 | 适用命令 | 说明 |
|---|---|---|
| `--src DIR` | prep/dub/post/run/status/report | 源视频目录 |
| `--out DIR` | 同上 | 成品目录；`report` 用它决定输出位置 |
| `--work DIR` | prep/dub/post/run | 中间目录，默认 `<out>/_work` |
| `--videos LIST` | prep/dub/post/run/status | `all`（默认）或逗号分隔的 stem/前缀 |
| `--source LANG` | prep/dub/run | 源语言，如 `en` |
| `--target LANG` | prep/dub/run | 目标语言，如 `zh-cn` |
| `--backend NAME` | dub/run | `index`（默认）/ `qwen3local` / `f5` |
| `--subtitle MODE` | dub/run | `hard`（默认）/ `none` / `soft` / `dual-hard` / `dual-soft` |
| `--mt-workers N` | prep/run | 翻译并发，默认取 `config.json` 的 4 |
| `--force` | prep/run | 重跑 ASR 与翻译；**不会**清逐句配音缓存 |
| `--allow-battery` | dub/run | 电池供电时仍允许跑 TTS；默认会拒绝并提示接电源 |
| `--video FILE` | qc | 成品 MP4 |
| `--source-video FILE` | qc | 源视频，用于时长/ASR 对照 |
| `--zh FILE` / `--en FILE` | qc/polish | 目标/源字幕 |
| `--glossary FILE` | qc/polish | 覆盖配置里的术语表 |
| `--json-out FILE` | qc/polish | qc：QC JSON 路径；polish：审校后的 SRT 路径 |
| `--logs DIR` | report | QC JSON 所在目录，默认技能内 `.runtime/logs/` |

`--src` / `--out` 未传时读 `config.json` 的 `src_dir` / `out_dir`；两者都没有会直接报错。

## 常用组合

```bash
# 指定某几条视频（stem 精确匹配或前缀匹配）
python scripts/pipeline.py run --src D:/videos --out D:/translated --videos 001,002

# 分成三步跑，便于观察/重试
python scripts/pipeline.py prep --src D:/videos --out D:/translated
python scripts/pipeline.py dub  --src D:/videos --out D:/translated
python scripts/pipeline.py post --src D:/videos --out D:/translated

# 软字幕（视频流可无损复制，播放器可开关）
python scripts/pipeline.py run --src D:/videos --out D:/translated --subtitle soft

# 单文件 QC
python scripts/pipeline.py qc --video D:/out/a.mp4 --source-video D:/videos/a.mp4 \
  --zh D:/out/_字幕/a.zh-cn.srt --en D:/out/_字幕/a.en.srt \
  --json-out D:/out/logs/a.qc.json

# 审校已有译文（块数与时间轴不变）
python scripts/pipeline.py polish --zh D:/out/_字幕/a.zh-cn.srt \
  --json-out D:/out/_字幕/a.polished.srt

# TTS 稳定性基准：100 句、不重启 daemon、采样 CPU/GPU/probe
python scripts/tts_stability.py --stem 002_Guidelines \
  --src "D:/course/M1_Module 1" --out "D:/out" \
  --zh "D:/out/_work/prep/002_Guidelines/zh-cn.srt" \
  --blocks 100 --warmup 5 --restart-every 0

# 换音色/后端后重标定配音时长模型（输出 fit_pred_a/b）
python scripts/fit_calibrate.py --pid 14080
```

## 退出码

| 码 | 含义 |
|---:|---|
| 0 | 全部成功（QC 未通过的视频也会让 `run` 返回 1） |
| 1 | 至少一个视频/阶段失败；批次中其他视频仍会继续 |
| 2 | 参数或前置条件错误（缺 `--src`/`--out`、venv 不存在等） |

`run` 结束后用 `status` 和 `<out>/QC_SUMMARY.md` 确认逐片结果，不要只看退出码。

## 直接调用 `trans_cli.py`（仅排查/单步时）

`pipeline.py` 已封装常见流程。只有需要单步调试或使用未封装能力时才直接调用：

| 子命令 | 作用 |
|---|---|
| `setup` / `selftest` / `doctor` / `check` | 安装与自检 |
| `channels` / `voices` / `test-channel` | 查渠道、查音色、验证 Key |
| `stt` | 音视频 → SRT |
| `sts` | 翻译已有 SRT |
| `tts` | SRT → 配音音频 |
| `vtv` | 完整视频翻译 |

直接调用时必须自己保证：输入不在输出目录、显式传 `--channel` / `--tts-channel` / `--asr-model`（上游默认值不安全）、dub-only 加 `--overwrite --no-clear-cache`。细节见 `references/troubleshooting-pyvideotrans.md` 与其 `--help`。
