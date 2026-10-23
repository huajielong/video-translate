"""参数契约：白名单、取值域、情感模式。客户端与守护进程共用。

这里的每一项都对应 `indextts/infer_v2.py` 里实测生效的参数。不在白名单里的
参数会被拒绝，而不是透传 —— 因为 `infer()` 会把未知 kwargs 一路传到
transformers 的 `_validate_model_kwargs`，在那里抛
`ValueError: The following model_kwargs are not used by the model`。
那个错误发生在模型已经加载完、即将开始推理的时刻，代价高且信息晦涩。
在这里提前拦掉，智能体只需付一次本地校验的成本。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errs import did_you_mean, usage

# ---------------------------------------------------------------------------
# 情感向量：顺序与 infer_v2.py:669 的 desired_vector_order 严格一致，不能改
# ---------------------------------------------------------------------------
EMO_VECTOR_ORDER = [
    "happy",
    "angry",
    "sad",
    "afraid",
    "disgusted",
    "melancholic",
    "surprised",
    "calm",
]
EMO_VECTOR_CN = ["喜", "怒", "哀", "惧", "厌恶", "低落", "惊喜", "平静"]

# 中文别名 -> 规范名
_EMO_ALIASES = {
    **{cn: en for cn, en in zip(EMO_VECTOR_CN, EMO_VECTOR_ORDER)},
    "高兴": "happy",
    "愤怒": "angry",
    "悲伤": "sad",
    "恐惧": "afraid",
    "反感": "disgusted",
    "低落": "melancholic",
    "惊讶": "surprised",
    "自然": "calm",
    "喜": "happy",
}


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # "float" | "int"
    lo: float
    hi: float
    default: float | int | None
    help: str
    normalize_zero: bool = False  # top_k=0 在 webui 里被转成 None（=禁用）
    step: float | None = None  # 给前端滑块用的步长；None 由前端按范围推断


GEN_PARAMS: dict[str, Param] = {
    "top_p": Param("top_p", "float", 0.0, 1.0, 0.8, "核采样累积概率阈值", step=0.01),
    "top_k": Param("top_k", "int", 0, 100, 30, "top-k 采样；0 表示禁用（等价于 None）", True, 1),
    "temperature": Param("temperature", "float", 0.05, 2.0, 0.8, "采样温度，越低越稳定", step=0.05),
    "length_penalty": Param("length_penalty", "float", -2.0, 2.0, 0.0, "长度惩罚", step=0.1),
    "num_beams": Param("num_beams", "int", 1, 10, 3, "beam search 宽度；默认 3 意味着三倍 GPT 开销", step=1),
    "repetition_penalty": Param(
        "repetition_penalty", "float", 0.1, 20.0, 10.0, "重复惩罚，偏高是上游默认", step=0.1
    ),
    "max_mel_tokens": Param(
        "max_mel_tokens", "int", 50, 1815, 1500, "单段生成的上限；过小会截断音频（见 warnings）", step=10
    ),
}

CTRL_PARAMS: dict[str, Param] = {
    "max_text_tokens_per_segment": Param(
        "max_text_tokens_per_segment", "int", 20, 600, 120, "分句 token 上限，越大分句越长", step=10
    ),
    "interval_silence": Param(
        "interval_silence", "int", 0, 10000, 200, "段间插入的静音毫秒数", step=10
    ),
}

# 明确拒绝的参数：有些是死参数，有些会造成灾难性输出
REJECTED_PARAMS = {
    "do_sample": "上游 infer_v2.py:507 硬编码 do_sample=True，此参数不生效",
    "verbose": "会打印整个 token 张量（infer_v2.py:479），灌爆日志",
    "typical_sampling": "webui 已注释并标注「不建议使用」",
    "typical_mass": "webui 已注释并标注「不建议使用」",
    "input_tokens": "时长控制用的上下文 token，非稳定接口",
    "use_speed": "model_v2.py 的 inference_speech 内从未使用该参数",
}

# ---------------------------------------------------------------------------
# 情感模式
# ---------------------------------------------------------------------------
EMO_MODE_NAMES = {
    0: "speaker",  # 音色即情感
    1: "audio",  # 情感参考音频
    2: "vector",  # 八维情感向量
    3: "text",  # 情感描述文本
}

# 每种模式下的 emo_alpha 默认值。依据：
#   mode1 -> webui.py:247 的滑块默认值 0.65
#   mode2 -> 向量本身已经表达了强度，不再额外缩放
#   mode3 -> CLAUDE.md 记录的上游推荐值 ~0.6
EMO_MODE_DEFAULT_ALPHA = {0: None, 1: 0.65, 2: 1.0, 3: 0.6}


def infer_emo_mode(
    *,
    emo_mode: int | None,
    emo_audio: str | None,
    emo_vec: list[float] | None,
    emo_text: str | None,
) -> int:
    """从用户给的参数推断情感模式；显式指定与参数冲突时报错而不是静默丢弃。

    静默丢弃对智能体是最坏的行为 —— 它无法从输出判断参数到底生效没有。
    """
    present = []
    if emo_audio:
        present.append((1, "--emo-audio"))
    if emo_vec is not None:
        present.append((2, "--emo-vec"))
    if emo_text is not None:
        present.append((3, "--emo-text"))

    if emo_mode is None:
        if len(present) > 1:
            names = "、".join(n for _, n in present)
            raise usage(
                f"情感参数互斥，同时给了 {names}",
                hint="只保留一个：--emo-audio（情感参考音频）/ --emo-vec（八维向量）/ --emo-text（情感描述文本）。",
            )
        return present[0][0] if present else 0

    if emo_mode not in EMO_MODE_NAMES:
        raise usage(
            f"--emo-mode 只能是 0/1/2/3，收到 {emo_mode}",
            hint="0=音色即情感，1=情感参考音频，2=八维向量，3=情感描述文本。",
        )

    # 显式指定了模式，但给了别的模式才用的参数
    for mode, flag in present:
        if mode != emo_mode:
            raise usage(
                f"--emo-mode {emo_mode} 与 {flag} 冲突",
                hint=f"{flag} 属于模式 {mode}（{EMO_MODE_NAMES[mode]}）。去掉其中一个。",
            )
    return emo_mode


def parse_emo_vec(expr: str) -> list[float]:
    """解析八维情感向量。

    支持三种写法：
      "0,0,1,0,0,0,0,0"     位置形式，必须正好 8 个数
      "哀=1" / "sad=1"       命名形式，未提及的分量记 0
      "0,0,1,0,0,0,0,0" 混 "哀=0.5"（命名形式覆盖前面的位置值）
    """
    expr = expr.strip()
    if not expr:
        raise usage("--emo-vec 为空", hint="例如：--emo-vec 哀=0.8 或 --emo-vec 0,0,0.8,0,0,0,0,0")

    values = [0.0] * 8

    if "=" in expr:
        for chunk in expr.replace("，", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                raise usage(
                    f"--emo-vec 片段无法解析：{chunk!r}",
                    hint="命名写法要求每一段都是 名称=数值，例如 哀=0.8,喜=0.2。",
                )
            key, _, raw = chunk.partition("=")
            key = key.strip()
            canon = _EMO_ALIASES.get(key) or (key.lower() if key.lower() in EMO_VECTOR_ORDER else None)
            if canon is None:
                raise usage(
                    f"未知情感分量：{key!r}",
                    hint="可用分量：" + "、".join(
                        f"{cn}({en})" for cn, en in zip(EMO_VECTOR_CN, EMO_VECTOR_ORDER)
                    ),
                )
            values[EMO_VECTOR_ORDER.index(canon)] = _parse_float(raw, f"--emo-vec {chunk}")
        return values

    parts = [p for p in expr.replace("，", ",").split(",") if p.strip()]
    if len(parts) != 8:
        raise usage(
            f"--emo-vec 位置写法需要正好 8 个数值，收到 {len(parts)} 个",
            hint="顺序为 " + "/".join(EMO_VECTOR_CN) + "，例如 --emo-vec 0,0,1,0,0,0,0,0",
        )
    return [_parse_float(p, "--emo-vec") for p in parts]


def coerce_emo_vec(value) -> list[float]:
    """把 JSON/CLI 传来的各种情感向量写法统一成 8 元 float 列表。

    支持：
      * 字符串：交给 `parse_emo_vec`（"哀=0.8" 或 "0,0,0.8,..."）
      * 字典：`{"哀": 0.8, "happy": 0.2}`（未提及的分量记 0）
      * 数组/元组：必须正好 8 个数值
    """
    if isinstance(value, str):
        return parse_emo_vec(value)
    if isinstance(value, dict):
        if not value:
            raise usage("emo_vec 为空", hint="例如 {\"哀\": 0.8} 或一次给 8 个分量。")
        expr = ",".join(f"{key}={raw}" for key, raw in value.items())
        return parse_emo_vec(expr)
    if isinstance(value, (list, tuple)):
        if len(value) != len(EMO_VECTOR_ORDER):
            raise usage(
                f"emo_vec 需要正好 {len(EMO_VECTOR_ORDER)} 个分量，收到 {len(value)} 个",
                hint="顺序为 " + "/".join(EMO_VECTOR_CN),
            )
        return [_parse_float(str(item), "emo_vec") for item in value]
    raise usage(
        f"emo_vec 需要字符串、对象或 {len(EMO_VECTOR_ORDER)} 元数组，"
        f"收到 {type(value).__name__}",
    )


def _parse_float(raw: str, where: str) -> float:
    try:
        value = float(raw.strip())
    except ValueError:
        raise usage(f"{where} 不是合法数值：{raw!r}") from None
    if value < 0 or value > 1:
        raise usage(
            f"{where} 必须在 0~1 之间：{value}",
            hint="情感分量取值 0~1。",
        )
    return value


def validate_gen_params(raw: dict) -> tuple[dict, list[str]]:
    """校验并归一化生成参数。返回 (干净参数, 提示信息列表)。"""
    clean: dict = {}
    notes: list[str] = []

    for key, value in raw.items():
        if value is None:
            continue
        if key in REJECTED_PARAMS:
            raise usage(
                f"参数 {key} 不被支持：{REJECTED_PARAMS[key]}",
                hint="运行 `indextts2 schema` 查看全部可用参数。",
            )
        param = GEN_PARAMS.get(key) or CTRL_PARAMS.get(key)
        if param is None:
            suggestion = did_you_mean(key, list(GEN_PARAMS) + list(CTRL_PARAMS))
            raise usage(
                f"未知参数：{key}" + (f"，是否想用 {suggestion}？" if suggestion else ""),
                hint="运行 `indextts2 schema` 查看全部可用参数。",
            )

        # bool 是 int 的子类，显式挡掉，避免 True 被当成 1
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise usage(f"参数 {key} 需要数值，收到 {type(value).__name__}: {value!r}")

        if value < param.lo or value > param.hi:
            raise usage(
                f"参数 {key} 超出范围 [{param.lo}, {param.hi}]：{value}",
                hint=f"{param.help}。",
            )

        if param.kind == "int":
            value = int(value)
            if param.normalize_zero and value == 0:
                clean[key] = None  # 与 webui.py:128 的 `int(top_k) if int(top_k) > 0 else None` 一致
                continue
        else:
            value = float(value)

        clean[key] = value

    return clean, notes


def default_gen_params() -> dict:
    """生成参数 + 控制参数的完整默认值集合。"""
    return {**{n: p.default for n, p in GEN_PARAMS.items()},
            **{n: p.default for n, p in CTRL_PARAMS.items()}}


def resolve_gen_params(raw: dict) -> dict:
    """把用户给的参数补成完整集合，再交给 infer()。

    全部显式传入（而不是依赖 infer 的默认值）是有意的：这样结果里回显的
    params 就是真正用到的值，智能体不必再去猜哪些参数生效了。
    """
    clean, _ = validate_gen_params(raw or {})
    merged = default_gen_params()
    merged.update(clean)
    return merged


def schema_payload() -> dict:
    """`indextts2 schema` 的输出：让智能体自省而不必读源码。"""
    return {
        "tool": "indextts2",
        "emotion_modes": {
            str(mode): {
                "name": name,
                "trigger": {
                    0: "不传任何情感参数",
                    1: "--emo-audio <路径或音色名>",
                    2: "--emo-vec <八维向量>",
                    3: "--emo-text <情感描述>",
                }[mode],
                "default_alpha": EMO_MODE_DEFAULT_ALPHA[mode],
            }
            for mode, name in EMO_MODE_NAMES.items()
        },
        "emotion_vector_order": [
            {"index": i, "cn": cn, "name": en}
            for i, (cn, en) in enumerate(zip(EMO_VECTOR_CN, EMO_VECTOR_ORDER))
        ],
        "params": {
            name: {
                "type": p.kind,
                "min": p.lo,
                "max": p.hi,
                "default": p.default,
                "step": p.step,
                "help": p.help,
            }
            for name, p in {**GEN_PARAMS, **CTRL_PARAMS}.items()
        },
        "rejected_params": REJECTED_PARAMS,
        "notes": [
            "num_beams 默认 3，意味着三倍 GPT 自回归开销；用 --fast 可降到 1。",
            "max_mel_tokens 上限来自 checkpoints/config.yaml 的 gpt.max_mel_tokens=1815。",
            "max_text_tokens_per_segment 上限 600，超了会 IndexError。",
            "参考音频超过 15 秒会被静默截断。",
            "输出固定为 22050 Hz / int16 / 单声道。",
            "do_sample 是死参数（上游硬编码 True），不对外暴露。",
        ],
    }
