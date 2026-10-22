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

## Windows 无控制台启动（重要）

从 Agent、编辑器或计划任务启动时，父进程没有控制台，Windows 会给控制台子进程新建窗口。
本项目分三层处理：

1. **入口**用 `pythonw scripts/pipeline.pyw`（GUI 子系统，不分配控制台）。
2. **所有 Python 子进程优先 base `pythonw`**：读 `venv/pyvenv.cfg` 的 `home`，
   用 `home\pythonw.exe`；venv 的 `site-packages` 通过 `PYTHONPATH` 传入
   （`_index_env` 与 `VTV_ITT_SITE`）。**不要**用 `venv\Scripts\pythonw.exe`：
   它是 uv launcher，会再启动 `uv\python\...\python.exe`，后者创建 `conhost`
   （空标题、一闪而过的黑窗）。
3. **不调用 `.cmd`**：`indextts2.cmd` 内部会启动 `powershell` / `where`，孙进程
   可能闪窗。stop 统一改为 `pythonw + scripts/indextts2.py stop --json`。
   `scripts/_winhide.py` 和 `_winhide_site/sitecustomize.py` 给所有 `Popen` 默认加
   `CREATE_NO_WINDOW`；只对显式 `DETACHED_PROCESS` / `CREATE_NEW_CONSOLE` 放行。

纯 `pythonw` 下 `sys.stdout` 为 `None`，`pipeline.py` / `trans_cli.py` 会把进度和
traceback 写到 `.runtime/logs/entry.log` / `trans_cli.log`。

排查：任务运行时看 `conhost.exe` 的父进程；正常不应有由 `pythonw` 创建的 conhost。
`serve` 后 API/shim/daemon 三条命令都应是 `uv\python\...\pythonw.exe`。

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
- 本项目对 vendor 的补丁见 `references/quality.md`、`references/pipeline.md`、
  `references/audio-fit.md`；升级上游后需重新应用：
  - `_rate.py` concat 强制 48k/2ch；空档 ≤natural 时保持 TTS 原速（不再把
    `audio_target` 默认设成整个槽位）；audio_native 下音频加速/放慢上限 1.0；
    首条字幕槽位从原始起点算，不把片头算进首句
  - `_stage_align.py` 使用 `VTV_TTS_SRT` 时不要用 TTS 单元覆盖显示字幕；对齐前把
    TTS 精确裁到首/末非静音，20ms fade，前后各加 80ms 纯静音，并从最终时间轴
    生成一行显示字幕
  - `_rate.py` 音频短于槽位时把视频最多加快 `max_video_speedup_rate`（默认 1.25）
    到“音频 + 300ms”，避免源空白留下 1–2s 死气；视频变速后更新队列时间轴
  - `_stage_subtitle.py` `cjk_len` 默认 22（与 `fit_wrap_width`、QC 一致）
  - `_stage_dubbing.py` 按时间重叠配对、逐行缓存、每视频固定参考（`clone_fixed.wav`）
  - `_stage_dubbing.py` 配音单元拟合：`fit_merge`/`fit_condense`（同说话人合并、
    实际缓存时长判断、DeepSeek 压缩、术语保护、`thinking=disabled`）；
    `line` 模式额外用 CAMPPlus 聚类做同说话人合并键；聚类子进程需要
    `VTV_ITT_SITE`（IndexTTS2 venv 的 `site-packages`），否则 `rc=1`、0 合并
  - `_stage_dubbing.py` 支持 `VTV_TTS_SRT`：TTS 用 DeepSeek 审校后的语义单元，
    显示字幕保持短块，`_save_srt_target` 不覆盖显示 SRT
  - `_stage_dubbing.py` / `tts/_index.py` 重启 daemon 时直接调用
    `pythonw + scripts/indextts2.py stop --json`，不经过 `indextts2.cmd`
  - `scripts/resegment_srt.py` 规则重断句；`scripts/resegment_check.py` DeepSeek
    审校（只允许合并/轻微压缩，失败回退规则）；`pipeline.py` 在 `prep` 前后调用，
    并生成 `<target>.tts.srt`
  - `pipeline.py` 的 `post` 在 `video_autorate` 下按成品自身时长输出，
    QC 用 `duration_tol_s`（默认 15s）允许变速带来的时长漂移
  - `scripts/trans_cli.py` 任务优先直接用 pyVideoTrans venv 的 `pythonw.exe`（fallback
    `uv run --no-sync`），并注入 `_winhide_site` 的 `PYTHONPATH`
  - `scripts/pipeline.py` / `trans_cli.py` 入口 `pythonw`、base pythonw 选择、
    `VTV_ITT_SITE`、旁路字幕导入、两遍 loudnorm、按成品时长 `-t` 裁剪
  - `scripts/pipeline.pyw` 无窗口入口；`scripts/_winhide.py` + `_winhide_site`
    统一 `CREATE_NO_WINDOW`；`scripts/gpu_lock_task.pyw` 替代 `.cmd`
  - `index-tts2-cli/scripts/itts/cli_net.py` `spawn_daemon` 用 base pythonw
    （`pyvenv.cfg` 的 `home`），避免 venv launcher 的 conhost
  - `_stage_translate.py` / `translate_srt.py` 超长块拆分
  - `_srt_wrap.py` 术语不拆；显示字幕按一行宽度切条
  - `_qwenttslocal.py` 纯 clone 只要求 Base
  - `cli.py` 修复 `video_autorate` 未写入
  - `configure/_paths.py` 支持 `VTV_DUBBING_CACHE` 隔离缓存
  - `tts/_index.py` 直连 `/v1/tts`、重启/绑核兜底
  - `scripts/gradio_shim.py` 把 Gradio 的临时参考路径按内容哈希归一化为 `.runtime/ref_cache/<sha256>.wav`；否则每句路径都变，参考缓存永远不命中
  - `index-tts2-cli/indextts/infer_v2.py` probe 字段、`INDEXTTS2_DIFFUSION_STEPS`
  - `index-tts2-cli/scripts/itts/srv.py` daemon 启动自绑定（High + `0xFFFF`）

> `pipeline.py serve --stop` 只停 API 和 shim；daemon 通常随后 idle 退出。若仍占用显存，
> 执行 `vendor/index-tts2-cli/venv/Scripts/python.exe vendor/index-tts2-cli/scripts/indextts2.py stop --force`。
> 在 Agent 场景优先用 `pipeline.py serve --stop`，避免这条命令的控制台窗口。
