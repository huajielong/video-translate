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

## 默认策略（B）

处理发生在 `dub` 内部、说话人聚类之后、TTS 之前；**不改视频、不切视频**。

### 1. `fit_merge`：合并同一说话人的相邻短句

- 条件：同一 `ref_wav`（同一说话人）、间隔 ≤`fit_gap_ms`（默认 500ms）、
  合并后 ≤`fit_max_chars`（默认 36）个汉字，且 `simple_wrap` 仍为 ≤2 行。
- 优先合并最超速的句子（worst-first），避免紧邻的好句被先吃掉。
- 合并后共用一次固定开销；时间轴取第一句开始到第二句结束，槽位自然变长。
- 不同说话人、间隔大的停顿、`line_roles` 指定了逐行音色的行都不合并。

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

- 配音短于槽位：最多放慢 `max_audio_slow_rate`（默认 1.2），但每句至少保留
  `dubb_min_pause_ms`（默认 200ms）停顿；再短的差值是静音。
- 配音长于槽位：Rubberband 保音色加速，`max_audio_speed_rate`（默认 2.5）只是兜底。
- 第一句不再把片头 4–5 秒算进自己的槽位。

## 配置

`config.json`（完整表见 `configuration.md`）：

```jsonc
{
  "fit_merge": true,
  "fit_condense": true,
  "fit_force": false,
  "fit_max_speed": 1.12,
  "fit_max_chars": 36,
  "fit_gap_ms": 500,
  "fit_pred_a": 1.30,
  "fit_pred_b": 0.123,
  "fit_condense_speed": 1.25,
  "max_audio_slow_rate": 1.2,
  "dubb_min_pause_ms": 200,
  "voice_autorate": true,
  "video_autorate": false
}
```

环境变量：`VTV_FIT_MERGE`、`VTV_FIT_CONDENSE`、`VTV_FIT_FORCE`、
`VTV_FIT_MAX_SPEED`、`VTV_FIT_MAX_CHARS`、`VTV_FIT_GAP_MS`、`VTV_FIT_PRED_A`、
`VTV_FIT_PRED_B`、`VTV_FIT_CONDENSE_SPEED`、`VTV_GLOSSARY_FILE`。

实现位置：`vendor/pyvideotrans/videotrans/task/_stage_dubbing.py` 的
`_fit_merge_queue`、`_fit_condense_queue`、`_fit_cached_ms`、`_fit_glossary_terms`。

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
| `clone_ref_mode=line` | 每行参考不同，基本不会合并 |
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
