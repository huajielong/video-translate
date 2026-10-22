# 配音自然度：单元拟合

## 什么时候看这份

- 短句被加速到 2–3×，长句赶、短句拖，整段“忽快忽慢”。
- 每句之间停顿长、听感生硬，音画时间轴却没变。
- 句间有“嗖”声/爆音，或想确认 80ms 预滚静音是否生效。
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
- 翻译后按句子边界重切；最终显示字幕按 **一行 22 字** 切条（CJK；非中文按
  `other_len`，默认 60）；
- `resegment_llm_check` 把整段字幕交给 DeepSeek 审校，输出 **TTS 语义单元**
  （001 最近一次：80 条一行显示字幕 → 31 个 TTS 单元）。显示字幕按 TTS 单元在
  最终音画时间轴上重新分配，字幕和语音/画面同步。

`dub` 阶段：

- `voice_autorate=false`、`max_audio_speed_rate=1.0`、`max_audio_slow_rate=1.0`：
  TTS 音频完全原速，不再为槽位拉伸/加速；
- `video_autorate=true`：音频比槽位长时，按片段 PTS 轻微慢放视频；音频短时把视频
  加快（上限 `max_video_speedup_rate`，默认 1.25）到“音频 + `max_phrase_pause_ms`”
  （默认 300ms），避免整段源空白变成死气；末段保留到片尾；
- 对齐前对每个 TTS 单元做边缘处理：精确裁到首/末非静音，20ms fade in/out，
  前后各加 **80ms 纯数字静音**；旧逻辑保留 1–2s 边缘静音会叠加成句间空白，
  保留边缘低电平噪声则会在拼接处产生“嗖”声；
- 有 TTS 语义单元时 `fit_merge`/`fit_condense` 不执行，避免二次改写音频；
- 视频变速后 `_rate.py` 会把 queue 时间更新到最终视频时间轴，显示字幕从该时间轴
  重新生成，保证字幕、语音、画面三者同步；
- `duration_tol_s`（默认 15s）允许视频变速带来的成品时长漂移。

实测 001（trial23）：音频加速 `False`、视频慢速 `True`，TTS 边缘裁剪 + 20ms fade +
80ms 预滚 + 视频快/慢微调后，成品 204.2s（源 214.5s，−10.3s），显示字幕 80 条、
全部一行、QC PASS；原来 14.1–16.5s 的 2.4s 句间空白消失，最大句间静音约 0.8s。

## fit 兜底（无 TTS 语义单元时）

处理发生在 `dub` 内部、说话人聚类之后、TTS 之前；fit 本身只改文本/时间，
是否对视频做局部变速由 `video_autorate` 决定。DeepSeek 审校不可用（无 Key/网络
失败）时，退回规则断句 + fit；以下为 fit 细节。

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
  "duration_tol_s": 15
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

默认 `fit_pred_a/b` 来自**中文 + IndexTTS2 的历史标定（beam=1/25 步）**。换后端、
音色、语言或 beam 后重跑：

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
| 字幕条数 | fit 兜底时会减少（002 232→199）；默认 TTS 单元路径每条一行，fallback 超长块可能两行 |

不解决的问题：源片本身的停顿（长静音）、视频时间轴变化、背景音乐；
需要背景音时按 `quality.md` 做人声分离/重新嵌入。

## 验证

1. 日志里每句有 `仅音频调整: dubb_duration=...ms, audio_target=...ms`；
   用最终 SRT 的槽位算 `dubb_duration / slot`，目标 **P90 ≤1.1、max ≤1.3**。
2. `tts_probe.jsonl` 用来看 GPT/s2mel 耗时，不要拿它当对齐比例。
3. 跑 `qc.py`：字幕块数、每条一行、术语命中、时长（`duration_tol_s`）。

fit 兜底历史实测（002，199 句）：中位 0.77×、P90 1.05×、最大 1.44×；
>1.5× 0 句、>1.3× 2 句；QC PASS，时长差 0.064s。

## 与 `video_autorate` 的关系

默认 `video_autorate=true`。本项目对上游实现做了补丁，当前行为是：

- 按字幕/TTS 语义单元切出视频片段，每段用 `setpts` 改变 PTS（libx264 重编码），
  音频轨不使用 Rubberband，保持 TTS 原速；
- 音频短于片段：最多把视频加快 `max_video_speedup_rate`（1.25×），让片段长度
  收到“音频 + `max_phrase_pause_ms`（300ms）”；末段不加快，避免切掉片尾；
- 音频长于片段：最多把视频慢放到 `max_video_pts_rate`（1.5×）；
- 变速后更新 `queue_tts` 到最终视频时间轴，显示字幕按最终时间轴重新生成；
- **首条字幕之前如果有片头，当前片段队列从首条字幕起点开始**；需要保留片头时，
  确认首条字幕从 0 开始，或关闭 `video_autorate`。片尾由最后一段保留到视频结束。

上游原版 `--video_autorate` 曾出现丢片头/片尾、片段内突然慢动作、QC 时长失败；
不要直接换回未打补丁的 vendor。全片统一微慢（1.03–1.05×）是另一个可选旋钮：
现在音频已经原速 + 视频局部微调，通常不需要再开，否则总时长和停顿都会变。

## 当前默认预设（audio_native）

```jsonc
{
  "clone_ref_mode": "line",
  "index_fast": false,
  "index_diffusion_steps": 25,
  "voice_autorate": false,
  "video_autorate": true,
  "max_audio_speed_rate": 1.0,
  "max_audio_slow_rate": 1.0,
  "max_video_speedup_rate": 1.25,
  "max_phrase_pause_ms": 300,
  "resegment": true,
  "resegment_llm_check": true,
  "subtitle_max_chars": 22,
  "duration_tol_s": 15
}
```

001（源 214.48s，trial23）实测：

| 指标 | audio_native |
|---|---:|
| 成品时长 | 204.21s（−10.27s） |
| 显示字幕 | 80 条，全部一行 |
| TTS 语义单元 | 31 |
| 最大句间静音 | 约 0.8s |
| 句间目标停顿 | 300ms |
| QC | PASS（`duration_tol_s=15`） |

历史 `quality_merge` 预设（`max_audio_slow_rate=1.05`）“轻微放慢填缝”只作为关闭
`resegment_llm_check` 后的 fit 兜底参考；它和当前“TTS 绝对原速”的目标不同。
如果只要速度，可设 `clone_ref_mode=cluster`、`index_fast=true`、
`index_diffusion_steps=15`，但会出现语速拖沓/情感偏平。

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
