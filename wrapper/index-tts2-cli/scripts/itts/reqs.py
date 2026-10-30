"""合成请求构造与守护进程调用。CLI / WebUI / API 共用。

这里刻意只依赖标准库与 itts 内的轻量模块，**不 import torch**：WebUI/API
进程本身不需要模型，只负责把请求翻译成与 `say` 完全一致的形态，再交给常驻
守护进程。所有参数校验都发生在本地（毫秒级），不会为了一个越界参数去碰 GPU。
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from . import spec
from . import voices as voices_mod
from .cli_net import cancel, ensure_daemon, get_job, submit, wait_for_job
from .conf import Config
from .errs import CliError, EXIT_DAEMON, EXIT_QUEUE_FULL, EXIT_USAGE

# OpenAI 兼容端点允许的音频格式。非 wav 由 API 层调用 ffmpeg 转换。
AUDIO_FORMATS: dict[str, tuple[str, str, list[str] | None]] = {
    "wav": (".wav", "audio/wav", None),
    "mp3": (".mp3", "audio/mpeg", ["-c:a", "libmp3lame", "-q:a", "4", "-f", "mp3"]),
    "flac": (".flac", "audio/flac", ["-c:a", "flac", "-f", "flac"]),
    "opus": (".opus", "audio/ogg", ["-c:a", "libopus", "-b:a", "48k", "-f", "ogg"]),
    "aac": (".aac", "audio/aac", ["-c:a", "aac", "-b:a", "128k", "-f", "adts"]),
    # OpenAI 的 pcm 是 24kHz / 16-bit / 小端 / 单声道裸流。
    "pcm": (".pcm", "audio/pcm", ["-f", "s16le", "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1"]),
}

_OPENAI_ONLY = {
    "model",
    "response_format",
    "speed",
    "instructions",
    "stream_format",
    "user",
    "metadata",
}

_SYNTH_FIELDS = {
    "text",
    "input",
    "voice",
    "output_name",
    "emo_mode",
    "emo_audio",
    "emo_vec",
    "emo_text",
    "emo_alpha",
    "emo_random",
    "seed",
    "fast",
    "params",
}

_PARAM_NAMES = set(spec.GEN_PARAMS) | set(spec.CTRL_PARAMS)
_ALLOWED_KEYS = _SYNTH_FIELDS | _PARAM_NAMES


# ---------------------------------------------------------------------------
# 输出路径
# ---------------------------------------------------------------------------
def default_output_path(cfg: Config, *, prefix: str = "indextts2", unique: bool = False) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    suffix = f"_{uuid.uuid4().hex[:6]}" if unique else ""
    return cfg.out_dir / f"{prefix}_{stamp}_{os.getpid() % 10000:04d}{suffix}.wav"


def absolute_path(path_str: str | None) -> str | None:
    """按**客户端自己的 cwd** 解析成绝对路径。

    守护进程的 cwd 被钉在项目根，所以相对路径必须在客户端这一侧就展开，
    否则同一个文件会因为两边 cwd 不同而解析成两个不同的缓存键。
    """
    if not path_str:
        return None
    return str(Path(path_str).expanduser().absolute())


def safe_output_name(raw: str) -> str:
    """把用户给的 output_name 收敛成 out_dir 下的纯文件名。

    只允许文件名，不允许任何路径成分；校验复用音色名的规则（Windows 保留
    设备名、首尾点、禁用的标点等），避免在 Windows 上写出 `CON.wav` 这类
    无法打开的名字。
    """
    name = str(raw).strip()
    if not name:
        raise CliError("BAD_USAGE", "output_name 不能为空", exit_code=EXIT_USAGE)
    if "/" in name or "\\" in name or name in (".", ".."):
        raise CliError(
            "BAD_USAGE",
            f"output_name 只能是纯文件名：{name!r}",
            hint="不能包含路径分隔符或 ..。",
            exit_code=EXIT_USAGE,
        )
    suffix = Path(name).suffix
    if suffix:
        if suffix.lower() != ".wav":
            raise CliError(
                "BAD_USAGE",
                f"输出固定为 wav，收到后缀 {suffix}",
                hint="output_name 请使用 .wav 后缀或省略后缀。",
                exit_code=EXIT_USAGE,
            )
        stem = Path(name).stem
    else:
        stem = name
    try:
        voices_mod.validate_voice_name(stem)
    except CliError as exc:
        detail = exc.message.replace("音色名", "文件名")
        raise CliError(
            "BAD_USAGE",
            f"output_name 不合法：{detail}",
            hint="换一个文件名。",
            exit_code=EXIT_USAGE,
        ) from None
    return stem + ".wav"


def resolve_output_path(
    cfg: Config,
    *,
    output: str | None = None,
    output_name: str | None = None,
    prefix: str = "indextts2",
    unique: bool = False,
) -> str:
    if output:
        return absolute_path(output) or ""
    if output_name:
        name = safe_output_name(output_name)
        path = cfg.out_dir / name
        if path.exists():
            path = cfg.out_dir / f"{Path(name).stem}_{uuid.uuid4().hex[:6]}.wav"
        return str(path)
    return str(default_output_path(cfg, prefix=prefix, unique=unique))


# ---------------------------------------------------------------------------
# 标量校验
# ---------------------------------------------------------------------------
def coerce_seed(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise CliError("BAD_USAGE", "seed 需要整数", exit_code=EXIT_USAGE)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise CliError("BAD_USAGE", f"seed 需要整数，收到 {value!r}", exit_code=EXIT_USAGE)


def wav_seconds(path: Path) -> float | None:
    """读 WAV 时长。非 WAV 或读不了就返回 None（仅用于提示与统计）。"""
    try:
        import wave

        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            if rate:
                return handle.getnframes() / float(rate)
    except Exception:
        return None
    return None


def _prepare_reference(cfg: Config, voice, role: str) -> tuple[str, list[str]]:
    src = Path(voice.path)
    if not src.is_file():
        raise CliError(
            "VOICE_UNREADABLE",
            f"{role}文件不存在：{src}",
            hint="运行 `indextts2 voices list --json` 查看可用音色。",
            exit_code=EXIT_USAGE,
        )
    normalized, warnings = voices_mod.normalize_audio(cfg, src)
    return str(normalized), warnings


# ---------------------------------------------------------------------------
# 请求构造
# ---------------------------------------------------------------------------
def build_request(
    cfg: Config,
    *,
    text: str,
    voice: str | None = None,
    output: str | None = None,
    output_name: str | None = None,
    output_prefix: str = "indextts2",
    output_unique: bool = False,
    emo_mode: int | None = None,
    emo_audio: str | None = None,
    emo_vec=None,
    emo_text: str | None = None,
    emo_alpha: float | None = None,
    emo_random: bool = False,
    seed=None,
    fast: bool = False,
    params: dict | None = None,
    max_text_tokens_per_segment: int | None = None,
    interval_silence: int | None = None,
) -> tuple[dict, list[str], dict]:
    """把一次合成意图翻译成守护进程请求。返回 (request, notes, voice_info)。"""
    if not isinstance(text, str) or not text.strip():
        raise CliError(
            "EMPTY_TEXT",
            "文本为空",
            hint="传入非空文本；注意纯标点/emoji 会在归一化后被抹掉。",
            exit_code=EXIT_USAGE,
        )

    notes: list[str] = []
    raw_params = dict(params or {})
    if max_text_tokens_per_segment is not None:
        raw_params["max_text_tokens_per_segment"] = max_text_tokens_per_segment
    if interval_silence is not None:
        raw_params["interval_silence"] = interval_silence
    if fast:
        raw_params["num_beams"] = 1

    resolved = spec.resolve_gen_params(raw_params)
    seg_tokens = resolved.pop("max_text_tokens_per_segment")
    interval = resolved.pop("interval_silence")

    voice_spec = voice or cfg.default_voice
    if not isinstance(voice_spec, str) or not voice_spec.strip():
        available = [v.name for v in voices_mod.list_voices(cfg)][:20]
        raise CliError(
            "VOICE_REQUIRED",
            "没有指定音色",
            hint=(
                "用 --voice / voice 指定音色名或路径。可用音色："
                + ("、".join(available) if available else "（音色库为空）")
                + "；运行 `indextts2 voices list --json` 看完整列表。"
            ),
            exit_code=EXIT_USAGE,
        )
    voice_obj = voices_mod.resolve(cfg, voice_spec)
    if not voice_obj.id:
        # 名字命中时 resolve() 不再哈希全部音色；这里只给选中的这一个补 id，
        # 保证 `say --json` 的 voice.id 字段与旧版一致。
        try:
            voice_obj.id = voices_mod.content_id(Path(voice_obj.path))
        except OSError:
            pass
    spk_path, warn = _prepare_reference(cfg, voice_obj, "音色参考音频")
    notes.extend(warn)

    vec = spec.coerce_emo_vec(emo_vec) if emo_vec is not None else None
    mode = spec.infer_emo_mode(
        emo_mode=emo_mode,
        emo_audio=emo_audio,
        emo_vec=vec,
        emo_text=emo_text,
    )
    # 显式指定模式时必须真的给出该模式需要的输入。旧代码在 mode=1 且没有
    # emo_audio 时会一路走到 voices.resolve(None) 抛 AttributeError。
    if mode == 1 and not emo_audio:
        raise CliError(
            "BAD_USAGE",
            "情感模式 1 需要情感参考音频",
            hint="传 --emo-audio / emo_audio（音色名或音频路径），或改用模式 0/2/3。",
            exit_code=EXIT_USAGE,
        )
    if mode == 2 and vec is None:
        raise CliError(
            "BAD_USAGE",
            "情感模式 2 需要八维情感向量",
            hint="传 --emo-vec / emo_vec（8 个数、命名对象或 '哀=0.8' 字符串）。",
            exit_code=EXIT_USAGE,
        )

    emo_audio_path = None
    if mode == 1:
        emo_voice = voices_mod.resolve(cfg, emo_audio)  # type: ignore[arg-type]
        emo_audio_path, warn = _prepare_reference(cfg, emo_voice, "情感参考音频")
        notes.extend(warn)

    if emo_alpha is not None:
        if isinstance(emo_alpha, bool) or not isinstance(emo_alpha, (int, float)):
            raise CliError("BAD_USAGE", "emo_alpha 需要 0~1 的数值", exit_code=EXIT_USAGE)
        alpha = float(emo_alpha)
        if not 0.0 <= alpha <= 1.0:
            raise CliError(
                "PARAM_OUT_OF_RANGE",
                f"emo_alpha 必须在 0~1 之间，收到 {alpha}",
                hint="mode1 内部对 alpha 不做 clamp，超过 1.0 会变成外插并产生怪异结果。",
                exit_code=EXIT_USAGE,
            )
        if mode == 0:
            notes.append("模式 0 下 --emo-alpha / emo_alpha 会被忽略（上游强制使用 1.0）")
    else:
        alpha = spec.EMO_MODE_DEFAULT_ALPHA[mode] or 1.0

    if emo_random and mode != 2:
        notes.append("--emo-random / emo_random 只在模式 2（八维向量）下生效，已忽略")

    if emo_text is not None and not isinstance(emo_text, str):
        raise CliError("BAD_USAGE", "emo_text 需要字符串", exit_code=EXIT_USAGE)

    request = {
        "text": text,
        "spk_audio": spk_path,
        "emo_mode": mode,
        "emo_audio": emo_audio_path,
        "emo_vec": vec,
        "emo_alpha": alpha,
        "emo_text": emo_text,
        "emo_random": bool(emo_random),
        "output_path": resolve_output_path(
            cfg,
            output=output,
            output_name=output_name,
            prefix=output_prefix,
            unique=output_unique,
        ),
        "max_text_tokens_per_segment": seg_tokens,
        "interval_silence": interval,
        "seed": coerce_seed(seed),
        "params": resolved,
    }
    voice_info = {"name": voice_obj.name, "id": voice_obj.id, "source": voice_obj.path}
    return request, notes, voice_info


# ---------------------------------------------------------------------------
# HTTP/JSON 请求体解析（WebUI/API 共用）
# ---------------------------------------------------------------------------
def _as_optional_str(payload: dict, key: str, *, strip: bool = False) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CliError("BAD_USAGE", f"{key} 需要字符串", exit_code=EXIT_USAGE)
    if strip:
        value = value.strip()
        return value or None
    return value


def _as_bool(payload: dict, key: str, default: bool = False) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise CliError("BAD_USAGE", f"{key} 需要布尔值", exit_code=EXIT_USAGE)
    return value


def _as_int(value, key: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CliError("BAD_USAGE", f"{key} 需要整数", exit_code=EXIT_USAGE)
    if not lo <= value <= hi:
        raise CliError(
            "PARAM_OUT_OF_RANGE",
            f"{key} 超出范围 [{lo}, {hi}]：{value}",
            exit_code=EXIT_USAGE,
        )
    return value


def parse_synth_payload(payload: object, *, max_text_chars: int | None = None) -> dict:
    """解析原生合成请求体，返回 build_request 可直接展开的字典。"""
    if not isinstance(payload, dict):
        raise CliError("BAD_REQUEST", "请求体必须是 JSON 对象", exit_code=EXIT_USAGE)

    unknown = sorted(set(payload) - _ALLOWED_KEYS)
    if unknown:
        raise CliError(
            "BAD_REQUEST",
            "未知字段：" + "、".join(unknown),
            hint="可用字段：" + "、".join(sorted(_ALLOWED_KEYS)) + "。",
            exit_code=EXIT_USAGE,
        )

    text = payload.get("text")
    input_text = payload.get("input")
    if text is not None and input_text is not None and text != input_text:
        raise CliError(
            "BAD_REQUEST",
            "text 与 input 同时存在但内容不同",
            hint="只保留一个；input 只是 OpenAI 风格的别名。",
            exit_code=EXIT_USAGE,
        )
    if text is None:
        text = input_text
    if not isinstance(text, str) or not text.strip():
        raise CliError("EMPTY_TEXT", "没有提供要合成的文本", exit_code=EXIT_USAGE)
    if max_text_chars is not None and len(text) > max_text_chars:
        raise CliError(
            "TEXT_TOO_LONG",
            f"文本 {len(text)} 字符，超过 API/WebUI 上限 {max_text_chars}",
            hint="拆成多段分别合成；CLI 的长文本可以用 `indextts2 say --text-file`。",
            exit_code=EXIT_USAGE,
        )

    emo_mode = payload.get("emo_mode")
    if emo_mode is not None:
        emo_mode = _as_int(emo_mode, "emo_mode", 0, 3)

    emo_alpha = payload.get("emo_alpha")
    if emo_alpha is not None:
        if isinstance(emo_alpha, bool) or not isinstance(emo_alpha, (int, float)):
            raise CliError("BAD_USAGE", "emo_alpha 需要 0~1 的数值", exit_code=EXIT_USAGE)
        emo_alpha = float(emo_alpha)

    nested = payload.get("params")
    if nested is None:
        nested = {}
    if not isinstance(nested, dict):
        raise CliError("BAD_USAGE", "params 需要对象", exit_code=EXIT_USAGE)
    if any(not isinstance(key, str) for key in nested):
        raise CliError("BAD_USAGE", "params 的键必须是字符串", exit_code=EXIT_USAGE)
    merged = dict(nested)
    # 平铺参数优先，方便 curl/简单客户端。
    for name in _PARAM_NAMES:
        if name in payload:
            merged[name] = payload[name]

    return {
        "text": text,
        "voice": _as_optional_str(payload, "voice", strip=True),
        "output_name": _as_optional_str(payload, "output_name", strip=True),
        "emo_mode": emo_mode,
        "emo_audio": _as_optional_str(payload, "emo_audio", strip=True),
        "emo_vec": payload.get("emo_vec"),
        "emo_text": _as_optional_str(payload, "emo_text"),
        "emo_alpha": emo_alpha,
        "emo_random": _as_bool(payload, "emo_random"),
        "seed": coerce_seed(payload.get("seed")),
        "fast": _as_bool(payload, "fast"),
        "params": merged,
    }


def parse_openai_speech(payload: object, *, max_text_chars: int | None = None) -> tuple[dict, str]:
    """解析 OpenAI /v1/audio/speech 请求体，返回 (原生请求字段, 音频格式)。"""
    if not isinstance(payload, dict):
        raise CliError("BAD_REQUEST", "请求体必须是 JSON 对象", exit_code=EXIT_USAGE)

    speed = payload.get("speed")
    if speed is not None:
        if isinstance(speed, bool) or not isinstance(speed, (int, float)):
            raise CliError("BAD_USAGE", "speed 需要数值", exit_code=EXIT_USAGE)
        if abs(float(speed) - 1.0) > 1e-6:
            raise CliError(
                "BAD_USAGE",
                f"当前只支持 speed=1.0，收到 {speed}",
                hint="模型本身不支持变速；需要变速时请对返回的音频用 ffmpeg atempo 处理。",
                exit_code=EXIT_USAGE,
            )

    stream_format = payload.get("stream_format")
    if stream_format not in (None, "audio"):
        raise CliError(
            "BAD_USAGE",
            f"不支持的 stream_format：{stream_format!r}",
            hint='只支持 "audio"（默认）。',
            exit_code=EXIT_USAGE,
        )

    raw_format = payload.get("response_format") or "mp3"
    if not isinstance(raw_format, str):
        raise CliError("BAD_USAGE", "response_format 需要字符串", exit_code=EXIT_USAGE)
    audio_format = raw_format.strip().lower()
    if audio_format not in AUDIO_FORMATS:
        raise CliError(
            "BAD_USAGE",
            f"不支持的 response_format：{raw_format!r}",
            hint="支持：" + "、".join(AUDIO_FORMATS) + "。",
            exit_code=EXIT_USAGE,
        )

    data = {key: value for key, value in payload.items() if key not in _OPENAI_ONLY}
    instructions = payload.get("instructions")
    if instructions is not None:
        if not isinstance(instructions, str):
            raise CliError("BAD_USAGE", "instructions 需要字符串", exit_code=EXIT_USAGE)
        has_emotion = any(
            key in data for key in ("emo_mode", "emo_audio", "emo_vec", "emo_text")
        )
        if instructions.strip() and not has_emotion:
            data["emo_text"] = instructions

    return parse_synth_payload(data, max_text_chars=max_text_chars), audio_format


# ---------------------------------------------------------------------------
# 守护进程调用
# ---------------------------------------------------------------------------
def _daemon_info(health: dict) -> dict:
    return {
        "pid": health.get("pid"),
        "port": health.get("port"),
        "instance_id": health.get("instance_id"),
        "was_cold_start": health.get("was_cold_start"),
        "model_load_s": health.get("model_load_s"),
    }


def submit_request(cfg: Config, request: dict) -> dict:
    """提交任务并把 503/非 202 响应翻译成 CliError。"""
    status, payload = submit(cfg, request)

    if status == 503:
        error = payload.get("error") or {}
        raise CliError(
            str(error.get("code") or "QUEUE_FULL"),
            str(error.get("message") or "守护进程当前无法接单"),
            hint=str(error.get("hint") or "稍后重试。"),
            exit_code=EXIT_QUEUE_FULL if error.get("code") == "QUEUE_FULL" else EXIT_DAEMON,
            retriable=True,
            stage="submit",
        )
    if status != 202 or not payload.get("ok"):
        error = payload.get("error") or {}
        raise CliError(
            str(error.get("code") or "SUBMIT_FAILED"),
            str(error.get("message") or f"提交失败（HTTP {status}）"),
            hint=str(error.get("hint") or "查看 daemon.log。"),
            exit_code=EXIT_DAEMON,
            retriable=True,
            stage="submit",
        )
    return payload


def run_request(
    cfg: Config,
    request: dict,
    *,
    background: bool = False,
    timeout: float = 3600,
    on_wait=None,
    on_progress=None,
) -> dict:
    """确保守护进程可用 → 提交 → （可选）等待结果。"""
    health = ensure_daemon(cfg, on_wait=on_wait)
    payload = submit_request(cfg, request)
    job_id = payload["job_id"]

    if background:
        return {
            "job_id": job_id,
            "state": "queued",
            "background": True,
            "queue_position": payload.get("queue_position"),
            "daemon": _daemon_info(health),
        }

    deadline = time.monotonic() + float(timeout)
    result = wait_for_job(cfg, job_id, deadline=deadline, on_progress=on_progress)
    result["job_id"] = job_id
    result["daemon"] = _daemon_info(health)
    return result


def job_status(cfg: Config, job_id: str) -> dict:
    payload = get_job(cfg, job_id)
    if payload.get("ok") is False:
        error = payload.get("error") or {}
        code = str(error.get("code") or "JOB_NOT_FOUND")
        raise CliError(
            code,
            str(error.get("message") or f"任务不存在：{job_id}"),
            hint=str(error.get("hint") or "任务可能已被清理；重新提交一次。"),
            exit_code=EXIT_DAEMON if code != "JOB_NOT_FOUND" else EXIT_USAGE,
        )
    return payload


def cancel_job(cfg: Config, job_id: str) -> dict:
    return cancel(cfg, job_id)


def default_voice(cfg: Config) -> str | None:
    """API/WebUI 没传 voice 时的默认值：config → voice_01 → 第一个音色。"""
    if cfg.default_voice:
        return cfg.default_voice
    names = [voice.name for voice in voices_mod.list_voices(cfg)]
    if not names:
        return None
    if "voice_01" in names:
        return "voice_01"
    return names[0]


__all__ = [
    "AUDIO_FORMATS",
    "absolute_path",
    "build_request",
    "cancel_job",
    "coerce_seed",
    "default_output_path",
    "default_voice",
    "job_status",
    "parse_openai_speech",
    "parse_synth_payload",
    "resolve_output_path",
    "run_request",
    "safe_output_name",
    "submit_request",
    "wav_seconds",
]
