"""命名音色库 + 参考音频的内容寻址缓存。

两个职责：

1. **命名**：让智能体用 `--voice 张三` 而不是去猜一个绝对路径。注册表存在
   skill 目录的 voices/voices.json，并自动发现项目 examples/ 与 prompts/ 下的
   音频（只记录路径，不复制文件）。

2. **内容寻址缓冲**：把任意格式的参考音频统一成 16-bit 单声道 PCM WAV，落在
   `state_dir/voices/<sha1>.wav`，然后把**这个路径**交给 infer()。

   为什么必须做这一步（上游 `infer_v2.py:372/429` 的缓存是按路径字符串比较的）：
     - 同一文件换内容不会让缓存失效 -> 用内容哈希当键就自然修好了
     - 同一个文件在不同客户端的大小写/斜杠写法不同 -> 会被当成两个键，
       每次都要重跑 wav2vec2-bert + CAMPPlus（还会 torch.cuda.empty_cache()）
     - librosa 读 mp3 要走 audioread 外部 ffmpeg，而 ffmpeg 不在 PATH 上
       （venv\\ffmpeg.exe 存在但目录没入 PATH）-> 显式用绝对路径调用

   注意这里**刻意不重采样**：音色参考音频还要拿去算 22050 Hz 的 ref_mel，
   降到 16k 会丢掉 8~11kHz 的内容。只统一容器、声道数和位深，保留原采样率。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

from .conf import SKILL_DIR, Config, ffmpeg_install_hint
from .errs import CliError, EXIT_VOICE_NOT_FOUND

AUDIO_EXTS = {
    ".wav",
    ".mp3",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".wma",
    ".webm",
    ".mp4",
    ".mkv",
}

REGISTRY_PATH = SKILL_DIR / "voices" / "voices.json"
VOICE_STORE_DIR = SKILL_DIR / "voices"


@dataclass
class Voice:
    name: str
    path: str
    id: str
    source: str  # "registered" | "auto" | "cache"
    notes: str = ""
    tags: list[str] | None = None

    def to_json(self) -> dict:
        out = {k: v for k, v in asdict(self).items() if v not in (None, "", [])}
        return out


# ---------------------------------------------------------------------------
# 注册表读写
# ---------------------------------------------------------------------------
def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {"voices": []}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"voices": []}
    if not isinstance(data.get("voices"), list):
        data["voices"] = []
    return data


def save_registry(data: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 唯一临时名：WebUI/API 多线程同时登记音色时，固定名会互相覆盖。
    tmp = REGISTRY_PATH.with_name(
        f"{REGISTRY_PATH.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp"
    )
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(REGISTRY_PATH)


def _resolve_entry_path(stored: str) -> Path:
    """把注册表里的路径解析成当前系统上的真实路径。

    新条目存的是相对 skill 根的 POSIX 风格路径（`voices/xxx.m4a`），跨系统复制
    后依然有效。历史条目存的是绝对路径 —— 从 Windows 拷到 Linux 后那个路径
    不存在，但同名文件就在 `voices/` 下，按文件名兜底找回。
    """
    path = Path(stored).expanduser()
    if not path.is_absolute():
        return SKILL_DIR / path
    if path.is_file():
        return path
    fallback = VOICE_STORE_DIR / path.name
    return fallback if fallback.is_file() else path


def _store_entry_path(path: Path) -> str:
    """能在 skill 内表达就存相对路径，否则存绝对路径。"""
    try:
        return path.relative_to(SKILL_DIR).as_posix()
    except ValueError:
        return str(path)


def content_id(path: Path) -> str:
    """参考音频内容哈希。用作缓存键与稳定 id。"""
    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 内容寻址的归一化缓存
# ---------------------------------------------------------------------------
def normalize_audio(cfg: Config, src: Path) -> tuple[Path, list[str]]:
    """把参考音频归一化到 state_dir/voices/<sha1>.wav，返回 (路径, 警告列表)。

    命中缓存时是纯文件系统查询，不调用 ffmpeg。
    """
    warnings: list[str] = []
    cid = content_id(src)
    dst = cfg.voice_cache_dir / f"{cid}.wav"
    if dst.is_file() and dst.stat().st_size > 44:  # 44 = 空 WAV 头长度
        return dst, warnings

    ffmpeg = cfg.ffmpeg_exe
    if ffmpeg is None:
        warnings.append(
            "未找到 ffmpeg（项目内与 PATH 上都没有），跳过音频归一化，"
            "直接使用原始文件；非 wav 格式可能在后续解码时失败。" + ffmpeg_install_hint()
        )
        return src, warnings

    cfg.voice_cache_dir.mkdir(parents=True, exist_ok=True)
    # 唯一临时名：API/WebUI 多个请求可能同时归一化同一份参考音频，
    # 固定名会让两个 ffmpeg 写同一个文件，产出损坏的 WAV。
    tmp = dst.with_name(f"{dst.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    cmd = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(src),
        "-vn",  # 丢掉视频流（mp4/mkv 容器）
        "-ac", "1",  # 单声道
        "-c:a", "pcm_s16le",  # 16-bit PCM
        # 必须显式指定包装格式：ffmpeg 靠扩展名推断输出 muxer，而 .tmp 不是
        # 任何已知格式，否则会以 "Error opening output files: Invalid argument" 失败。
        "-f", "wav",
        # 刻意不加 -ar：保留原始采样率，见模块 docstring
        str(tmp),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace"
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        tmp.unlink(missing_ok=True)
        raise CliError(
            "VOICE_UNREADABLE",
            f"参考音频处理失败：{src}（{exc}）",
            hint="确认文件是完整可读的音频。",
            exit_code=EXIT_VOICE_NOT_FOUND,
        ) from exc

    if proc.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        detail = (proc.stderr or "").strip().splitlines()
        detail_str = detail[-1] if detail else f"ffmpeg 退出码 {proc.returncode}"
        raise CliError(
            "VOICE_UNREADABLE",
            f"参考音频无法解码：{src}（{detail_str}）",
            hint="换成 wav/mp3/flac 等常见格式再试。",
            exit_code=EXIT_VOICE_NOT_FOUND,
        )

    tmp.replace(dst)
    return dst, warnings


# ---------------------------------------------------------------------------
# 发现与解析
# ---------------------------------------------------------------------------
def _scan_dir(directory: Path, source: str) -> list[Voice]:
    if not directory.is_dir():
        return []
    found = []
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix.lower() in AUDIO_EXTS:
            found.append(
                Voice(name=entry.stem, path=str(entry), id="", source=source)
            )
    return found


def discover(cfg: Config) -> list[Voice]:
    """注册表条目 + 自动发现。自动发现的排后面，同名时注册表优先。"""
    registered = []
    for raw in load_registry().get("voices", []):
        if not isinstance(raw, dict) or not raw.get("name") or not raw.get("path"):
            continue
        registered.append(
            Voice(
                name=str(raw["name"]),
                path=str(_resolve_entry_path(str(raw["path"]))),
                id=str(raw.get("id") or ""),
                source="registered",
                notes=str(raw.get("notes") or ""),
                tags=list(raw.get("tags") or []) or None,
            )
        )

    seen = {v.name.lower() for v in registered}
    auto: list[Voice] = []
    for directory, source in (
        (cfg.project_dir / "examples", "auto"),
        (cfg.project_dir / "prompts", "auto"),
        (VOICE_STORE_DIR, "auto"),
    ):
        for voice in _scan_dir(directory, source):
            if voice.name.lower() not in seen:
                seen.add(voice.name.lower())
                auto.append(voice)

    return registered + auto


def list_voices(cfg: Config, *, with_ids: bool = False) -> list[Voice]:
    voices = discover(cfg)
    if with_ids:
        for voice in voices:
            if not voice.id:
                path = Path(voice.path)
                try:
                    if path.is_file():
                        voice.id = content_id(path)
                except OSError:
                    # 文件刚好被删除/不可读：保留 id 为空，不要拖垮整个列表。
                    pass
    return voices


def resolve(cfg: Config, spec: str) -> Voice:
    """把 `--voice` 的取值解析成 Voice。

    接受三种形式：音色名、内容 id（sha1，可带 "sha1:" 前缀）、或直接给路径。
    """
    spec = spec.strip()
    if not spec:
        raise _not_found(cfg, "音色不能为空")

    # 1) 直接当路径
    candidate = Path(spec).expanduser()
    if candidate.is_file():
        return Voice(
            name=candidate.stem,
            path=str(candidate.resolve()),
            id=content_id(candidate.resolve()),
            source="path",
        )

    lowered = spec.lower()

    # 2) 按名字。先不计算内容 id —— 命中名字时完全不必读文件内容。
    for voice in list_voices(cfg):
        if voice.name.lower() == lowered:
            return voice

    # 3) 按 id（sha1 前缀）。只有名字没命中才做哈希。
    wanted = lowered[5:] if lowered.startswith("sha1:") else lowered
    for voice in list_voices(cfg, with_ids=True):
        if voice.id and voice.id.lower().startswith(wanted):
            return voice

    # 4) 路径形式但文件不存在
    if any(sep in spec for sep in ("\\", "/")) or Path(spec).suffix.lower() in AUDIO_EXTS:
        raise _not_found(
            cfg,
            f"没有找到名为 {spec!r} 的音色，且它也不像一个存在的文件路径",
        )
    raise _not_found(cfg, f"未知音色：{spec!r}")


def _not_found(cfg: Config, message: str) -> CliError:
    voices = list_voices(cfg)
    names = [v.name for v in voices][:20]
    hint = (
        "可用音色：" + "、".join(names)
        if names
        else "音色库为空；用 `indextts2 voices add <名称> <音频路径>` 添加。"
    )
    return CliError(
        "VOICE_NOT_FOUND",
        message,
        hint=hint + "（运行 `indextts2 voices list --json` 看完整列表）",
        exit_code=EXIT_VOICE_NOT_FOUND,
    )


_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def validate_voice_name(name: str) -> str:
    """校验音色名。

    名字会被直接用作文件名，所以必须挡掉路径分隔符与 `..` —— POSIX 上 `/` 是
    真实分隔符，`voices add ../evil ...` 会写到 voices/ 之外。
    """
    name = (name or "").strip()
    if not name:
        raise CliError("BAD_VOICE_NAME", "音色名不能为空", hint="换一个名字。")
    if len(name) > 64:
        raise CliError(
            "BAD_VOICE_NAME",
            f"音色名过长（{len(name)} 字符，上限 64）",
            hint="用短一点的名字。",
        )
    forbidden = set('/\\:*?"<>|')
    if any(ch in forbidden for ch in name) or name in (".", ".."):
        raise CliError(
            "BAD_VOICE_NAME",
            f"音色名 {name!r} 含有不允许的字符",
            hint='音色名会作为文件名使用，不能包含 / \\ : * ? " < > |，也不能是 . 或 ..',
        )
    if name.startswith(".") or name.endswith((".", " ")):
        raise CliError(
            "BAD_VOICE_NAME",
            f"音色名 {name!r} 不能以点开头或以点/空格结尾",
            hint="换一个名字。",
        )
    if name.split(".")[0].upper() in _WINDOWS_RESERVED:
        raise CliError(
            "BAD_VOICE_NAME",
            f"音色名 {name!r} 与 Windows 保留设备名冲突",
            hint="换一个名字（CON/PRN/AUX/NUL/COM1-9/LPT1-9 不可用）。",
        )
    return name


def add_voice(name: str, src: Path, *, notes: str = "", tags: list[str] | None = None) -> Voice:
    """把一个音频复制进 skill 的 voices/ 目录并登记。"""
    name = validate_voice_name(name)
    if not src.is_file():
        raise CliError(
            "VOICE_UNREADABLE",
            f"音频文件不存在：{src}",
            hint="确认路径正确。",
            exit_code=EXIT_VOICE_NOT_FOUND,
        )
    if src.suffix.lower() not in AUDIO_EXTS:
        raise CliError(
            "VOICE_UNREADABLE",
            f"不支持的音频格式：{src.suffix}",
            hint="支持 " + "、".join(sorted(AUDIO_EXTS)) + "。",
            exit_code=EXIT_VOICE_NOT_FOUND,
        )

    data = load_registry()
    if any(str(v.get("name", "")).lower() == name.lower() for v in data["voices"]):
        raise CliError(
            "VOICE_EXISTS",
            f"音色 {name!r} 已存在",
            hint="换个名字，或先 `indextts2 voices rm` 删掉旧的。",
        )

    VOICE_STORE_DIR.mkdir(parents=True, exist_ok=True)
    dst = VOICE_STORE_DIR / f"{name}{src.suffix.lower()}"
    if dst.exists():
        dst = VOICE_STORE_DIR / f"{name}-{content_id(src)[:8]}{src.suffix.lower()}"
    shutil.copy2(src, dst)

    voice = Voice(
        name=name,
        path=str(dst),
        id=content_id(dst),
        source="registered",
        notes=notes,
        tags=tags or None,
    )
    entry = voice.to_json()
    # 存相对路径：整份 skill 拷到别的系统后仍然有效
    entry["path"] = _store_entry_path(dst)
    data["voices"].append(entry)
    save_registry(data)
    return voice


def remove_voice(name: str) -> Voice:
    data = load_registry()
    for idx, raw in enumerate(data["voices"]):
        if str(raw.get("name", "")).lower() == name.lower():
            removed = data["voices"].pop(idx)
            save_registry(data)
            path = _resolve_entry_path(str(removed.get("path", "")))
            # 只删登记时复制进 voices/ 的那份，不动用户在别处的原始文件
            if path.is_file() and path.parent.resolve() == VOICE_STORE_DIR.resolve():
                path.unlink(missing_ok=True)
            return Voice(
                name=str(removed.get("name")),
                path=str(removed.get("path")),
                id=str(removed.get("id") or ""),
                source="registered",
            )
    raise CliError(
        "VOICE_NOT_FOUND",
        f"音色库中没有 {name!r}",
        hint="运行 `indextts2 voices list` 查看已登记的音色。",
        exit_code=EXIT_VOICE_NOT_FOUND,
    )


def rename_voice(old: str, new: str) -> Voice:
    new = validate_voice_name(new)
    data = load_registry()
    for raw in data["voices"]:
        if str(raw.get("name", "")).lower() == new.lower():
            raise CliError(
                "VOICE_EXISTS",
                f"音色 {new!r} 已存在",
                hint="换个新名字。",
            )
    for raw in data["voices"]:
        if str(raw.get("name", "")).lower() == old.lower():
            raw["name"] = new
            save_registry(data)
            return Voice(
                name=new,
                path=str(raw.get("path")),
                id=str(raw.get("id") or ""),
                source="registered",
            )
    raise CliError(
        "VOICE_NOT_FOUND",
        f"音色库中没有 {old!r}",
        hint="运行 `indextts2 voices list` 查看已登记的音色。",
        exit_code=EXIT_VOICE_NOT_FOUND,
    )
