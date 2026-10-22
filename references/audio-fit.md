# 配音自然度：单元拟合

## 什么时候看这份

- 短句被加速到 2–3×，长句赶、短句拖，整段“忽快忽慢”。
- 每句之间停顿长、听感生硬，音画时间轴却没变。
- 想在**不改视频时长/不切视频**的前提下，把配音速度压回 1.1× 左右。

002 旧版实测：232 句里 **21 句需要 >1.5×**，最大 **2.75×**；合并+压缩后 199 句，
最大 **1.44×**，>1.5× 为 **0**。

## 根因

IndexTTS2 **每句有固定开销**（中文实测约 `1.3s + 0.123s/汉字`）：5 个汉字也要约
1.6 秒音频。而槽位是“本句开始 → 下一句开始”，不是“这句话需要多久”。所以：

- 极短槽位（0.7–1.5s）里的碎片句，单靠缩短文字救不了；
- 长译文塞进短槽位，只能 2–3× 加速；
- 逐句独立变速会让播放速度在 0.83–2.27× 之间跳。

## 默认策略 audio_native（声音优先）

`prep` 先做语义重断句：

- 翻译前合并源碎句（`resegment_srt.py`，源 74 → 45 左右）；
- 翻译后按句子边界重切；最终显示字幕按 **一行 22 字** 切条；
- `resegment_llm_check` 把整段字幕交给 DeepSeek 审校，输出 **TTS 语义单元**
  （001 实测 83 条一行显示字幕 → 31 个 TTS 单元）。显示字幕按 TTS 单元在最终
  音画时间轴上重新分配，字幕和语音/画面同步。

`dub` 阶段：

- `voice_autorate=false`、`max_audio_speed_rate=1.0`、`max_audio_slow_rate=1.0`：
  TTS 音频完全原速，不再为槽位拉伸；
- `video_autorate=true`：音频比槽位长时，按片段 PTS 轻微慢放视频；音频短时保留
  自然静音；
- 有 TTS 语义单元时关闭 `fit_merge`/`fit_condense`，避免二次改写音频；
- `duration_tol_s`（默认 10s）允许视频变速带来的成品时长漂移。

实测 001：音频加速 `False`、视频慢速 `True`，成品 220.1s（源 214.5s，+5.6s），
显示字幕 83 条、全部一行、QC PASS。

## fit 兜底（无 TTS 语义单元时）

处理发生在 `dub` 内部、说话人聚类之后、TTS 之前；**不改视频、不切视频**。
DeepSeek 审校不可用（无 Key/网络失败）时，退回规则断句 + fit；以下为 fit 细节。

### 1. `fit_merge`：合并同一说话人的相邻短句

- 条件：同一说话人（`cluster`/`single` 用 `ref_wav`，`line` 用 CAMPPlus 聚类键）、
  间隔 ≤`fit_gap_ms`（默认 500ms）、
  合并后 ≤`fit_max_chars`（默认 44）个汉字；最终显示按一行
  `subtitle_max_chars`（默认 22 个汉字）切条。
- 优先合并最超速的句子（worst-first），避免紧邻的好句被先吃掉。
- **语义半句优先**：如果前一句不以 `。！？.!?…` 结尾（以逗号、冒号或没有标点收尾），
  且与后一句同说话人、间隔 ≤900ms，即使两边都不超速也合并。这类块通常是
  ASR/翻译把一句完整的话切成了两段；不合并会让 TTS 在句中换气，听感断裂。
- 合并后共用一次固定开销；时间轴取第一句开始到第二句结束，槽位自然变长。
- 不同说话人、间隔大的停顿、`line_roles` 指定了逐行音色的行都不合并。

**聚类是 `line` 模式合并的前提。** 逐句参考路径不同，只有 CAMPPlus 聚类给出
`_fit_speaker` 后，`fit_merge` 才有可用的“同说话人”键。聚类子进程用 base
pythonw，必须通过 `VTV_ITT_SITE=venv\Lib\site-packages` 拿到 torch/campplus。
缺失时日志会出现：

```text
speaker clustering failed rc=1
[fit] merge 78 -> 78 lines (0 merges)
```

成品块数会接近 prep 原始块数（例如 80 → 78），而不是合并后的 57 左右。确认
`VTV_ITT_SITE` 指向 IndexTTS2 venv 的 `site-packages` 后重跑 `dub`。

### 2. `fit_condense`：只压缩仍然超速的句子

- 触发：合并后的“时长/槽位” > `fit_condense_speed`（默认 1.25×）。
- 判断优先用 `DUBBING_CACHE` 里**这条文本的真实音频时长**（重跑时最准）；
  新行/合并行没有缓存，用固定开销模型预测。
- 目标：压到 `fit_max_speed`（默认 1.12×）对应的字数。
- DeepSeek 只返回改写句；术语表命中的词和英文技术词（JSON、GPT、get_completion）
  必须保留，丢失则拒绝本次压缩；超过目标会再压一次。
- `deepseek-flash` 是 reasoning 模型，调用必须
  `extra_body={"thinking":{"type":"disabled"}}` 且 `max_tokens≥512`，
  否则 token 全花在 reasoning 上、`content` 为空。

### 3. 音频填缝（已有逻辑）

- audio_native 下音频完全原速，不做填缝/加速；音频短于槽位就保留自然静音。
- 仅当关闭 `video_autorate`、退回音频对齐时才使用：空档 ≤`dubb_natural_pause_ms`
  （默认 600ms）保持原速；超过才最多放慢 `max_audio_slow_rate`。`dubb_min_pause_ms`
  （默认 200ms）是底线。
- 配音长于槽位：`max_audio_speed_rate` 是兜底；audio_native 下为 1.0。
- 第一句不再把片头 4–5 秒算进自己的槽位。

## 配置

`config.json`（完整表见 `configuration.md`）：

```jsonc
{
  "fit_merge": true,
  "fit_condense": true,
  "fit_force": false,
  "fit_max_speed": 1.12,
  "fit_max_chars": 44,
  "fit_wrap_width": 22,
  "fit_gap_ms": 500,
  "fit_pred_a": 1.30,
  "fit_pred_b": 0.123,
  "fit_condense_speed": 1.25,
  "clone_ref_mode": "line",
  "index_fast": false,
  "index_diffusion_steps": 25,
  "resegment": true,
  "resegment_llm_check": true,
  "resegment_target_max_chars": 44,
  "max_audio_speed_rate": 1.0,
  "max_audio_slow_rate": 1.0,
  "dubb_min_pause_ms": 200,
  "dubb_natural_pause_ms": 600,
  "voice_autorate": false,
  "video_autorate": true,
  "duration_tol_s": 10
}
```

这是默认的 **audio_native** 预设：TTS 原速、视频片段吸收长度差、DeepSeek 审校
语义单元；`fit_*` 只在没有 DeepSeek 审校结果时兜底。比 beam1/15 的快速配置慢约 17%。

环境变量：`VTV_FIT_MERGE`、`VTV_FIT_CONDENSE`、`VTV_FIT_FORCE`、
`VTV_FIT_MAX_SPEED`、`VTV_FIT_MAX_CHARS`、`VTV_FIT_WRAP_WIDTH`、
`VTV_FIT_GAP_MS`、`VTV_FIT_PRED_A`、`VTV_FIT_PRED_B`、
`VTV_FIT_CONDENSE_SPEED`、`VTV_GLOSSARY_FILE`、
`VTV_ITT_SITE`（聚类用的 IndexTTS2 `site-packages`）。

实现位置：`vendor/pyvideotrans/videotrans/task/_stage_dubbing.py` 的
`_cluster_refs`、`_fit_merge_queue`、`_fit_condense_queue`、`_fit_cached_ms`、
`_fit_glossary_terms`。

## 标定预测模型

默认值是在**中文 + IndexTTS2 beam=1** 上拟合的。换后端、音色、语言或 beam 后重跑：

```bash
python scripts/fit_calibrate.py --pid <daemon_pid>
```

它读 `.runtime/logs/tts_probe.jsonl` 的 `chars` 和 `audio`，做最小二乘：

```text
audio_seconds ≈ fit_pred_a + fit_pred_b × 汉字数
```

把输出的 `fit_pred_a` / `fit_pred_b` 写入 `config.json`。如果换了语言（非中文），
拟合前把 `fit_merge`/`fit_condense` 设为 `false` 或先确认字符计数口径一致。

## 适用边界

| 场景 | 是否生效 |
|---|---|
| 目标语言 `zh*`、IndexTTS2、硬/软字幕 | 生效 |
| 双语字幕（`dual-hard`/`dual-soft`） | **不生效**：源/目标行数必须配对 |
| `clone_ref_mode=cluster` 多说话人 | 生效，只合并同一参考的行 |
| `clone_ref_mode=line` | 生效：逐句参考保留情感，另用 CAMPPlus 聚类做同说话人合并键 |
| 非中文 / 非 IndexTTS2 | 默认不启用；重新标定预测模型后设 `fit_force=true` |
| 字幕条数 | 会减少（002 232→199），每条仍 ≤2 行 |

不解决的问题：源片本身的停顿（长静音）、视频时间轴变化、背景音乐；
需要背景音时按 `quality.md` 做人声分离/重新嵌入。

## 验证

1. 日志里每句有 `仅音频调整: dubb_duration=...ms, audio_target=...ms`；
   用最终 SRT 的槽位算 `dubb_duration / slot`，目标 **P90 ≤1.1、max ≤1.3**。
2. `tts_probe.jsonl` 用来看 GPT/s2mel 耗时，不要拿它当对齐比例。
3. 跑 `qc.py`：字幕块数、≤2 行、术语命中、时长。

002 最终实测（199 句）：中位 0.77×、P90 1.05×、最大 1.44×；
>1.5× 0 句、>1.3× 2 句；QC PASS，时长差 0.064s。

## 与 `video_autorate` 的关系

上游的 `--video_autorate` 会按字幕片段重建视频：**丢片头/片尾、232 段逐段重编码、
片段内画面突然慢动作**，还会让 QC 时长失败。目前默认关闭。

- 新视频优先用 B（合并+压缩）；它不碰视频，片头片尾完整。
- 只有“纯幻灯片/录屏、没有口型、文字已定稿、必须保持总时长”才考虑修 A。
- 全片统一微慢（1.03–1.05×）是另一个可选旋钮；合并已经够用时不要开，
  否则槽位一起变长、停顿可能增加。

## 质量优先预设（quality_merge）

默认配置就是这一组：

```jsonc
{
  "clone_ref_mode": "line",
  "index_fast": false,
  "index_diffusion_steps": 25,
  "max_audio_slow_rate": 1.05,
  "dubb_natural_pause_ms": 600
}
```

vLLM 001 实测（214.48s）：

| 指标 | quality_merge |
|---|---:|
| 实际语速 | 0.95–1.23×，中位 1.00× |
| >1.5× 句子 | 0 |
| 字幕块数 | 57，≤2 行 |
| 静音 / 最长静音 | 26.9s / 1.47s |
| F0 中位 / IQR | 110.7 Hz / 30.4（10.20 为 111.3 / 31.1） |
| 时长差 / 响度 | 0.000s / -18.0 LUFS |
| TTS 耗时 | 398s（v1 快速配置 339s，约 +17%） |

代价是 TTS 更慢、逐句参考的音色一致性略低于 cluster 单参考；如果只要速度，
可设 `clone_ref_mode=cluster`、`index_fast=true`、`index_diffusion_steps=15`，
但会出现语速拖沓/情感偏平。

## 常见问题

**fit 没生效**
检查目标语言是否 `zh*`、字幕是否双语、`VTV_FIT_MERGE` 是否为 `0`；
日志应有 `[fit] merge N -> M lines`。

**`[fit] condense 0 lines`**
可能本来就没有 >1.25× 的句子；或 DeepSeek Key 缺失/调用失败（看 warning）；
`deepseek-flash` 的 thinking 没关时 `content` 会为空。

**压缩被拒绝**
日志有 `[fit] condense reject ... term lost` 或 `over target`；
术语/英文词不能丢，宁可保留原句。可以调低 `fit_condense_speed` 减少触发，
或换更强的改写模型。

**字幕条数变少**
这是合并短句的正常结果；要逐句字幕设 `fit_merge=false`，但短句加速会回来。

**预测偏差大**
用 `fit_calibrate.py` 重新拟合；`tts_probe.jsonl` 至少要有几十条不同字数的记录。
