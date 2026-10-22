#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trans_cli.py — self-contained CLI wrapper around pyVideoTrans.

Why this wrapper exists
-----------------------
pyVideoTrans ships a capable but sharp-edged CLI. This wrapper makes it safe
and predictable for both humans and agents:

  * output directories are chosen safely. Upstream runs `shutil.rmtree` on
    --output-dir at the start of every task, so pointing it at a folder that
    contains your source files deletes them.
  * translation / TTS channels are selected by name, not magic indices.
  * API keys are injected from environment variables or --api-key.
  * child processes run in UTF-8 mode (Chinese Windows otherwise mangles logs).
  * results are verified and summarized.

Commands
--------
  setup           provision the runtime (cache first, download only what is missing)
  selftest        end-to-end check: ffmpeg, ASR, TTS, optional translation
  doctor          detect OS/arch/GPU/tools/caches
  check           verify the installation and show configured channels
  channels        list translation and TTS channels
  voices          list Edge-TTS voices for a language
  test-channel    send one tiny request to verify an AI channel's credentials
  vtv             translate a video (ASR -> translate -> TTS -> mux)
  stt             transcribe audio/video to SRT
  sts             translate an SRT subtitle file
  tts             synthesize speech from an SRT

Examples
--------
  python trans_cli.py check
  python trans_cli.py vtv movie.mp4 --source zh-cn --target en
  python trans_cli.py vtv movie.mp4 --source zh-cn --target en --channel deepseek
  python trans_cli.py stt talk.mp4
  python trans_cli.py sts subs.srt --source zh-cn --target ja
  python trans_cli.py tts subs.srt --target en --voice en-US-GuyNeural

Environment
-----------
  PYVIDEOTRANS_HOME   pyVideoTrans checkout (default: vendored runtime, then ~/pyvideotrans)
  UV_BIN / FFMPEG_BIN / FFPROBE_BIN / OLLAMA_BIN  explicit tool paths
  HTTPS_PROXY         used for downloads and cloud APIs (git config is a fallback)
  DEEPSEEK_API_KEY, OPENAI_API_KEY, ZHIPUAI_API_KEY, ...  channel credentials
  TRANS_ASR_MODEL     default ASR model (default: large-v3-turbo)

Exit codes: 0 success, 1 task failed, 2 wrapper/precondition error.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import NoReturn

VERSION = "1.3.0"

# Layout: <skill>/scripts/trans_cli.py
#         <skill>/vendor/pyvideotrans   (tracked upstream source)
#         <skill>/tools                 (provisioned, untracked)
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from _winhide import hide_console_windows  # noqa: E402

hide_console_windows()

SKILL_DIR = SCRIPT_DIR.parent
BUNDLED_HOME = SKILL_DIR / "vendor" / "pyvideotrans"

# Provisioned tools and download cache (cache-first: never re-download what is here).
TOOLS_DIR = SKILL_DIR / "tools"
DOWNLOAD_CACHE = TOOLS_DIR / "_cache"

# Pre-1.3 layout, used only by `setup` for one-time state migration.
LEGACY_PACKAGE_ROOT = SKILL_DIR.parent.parent.parent
LEGACY_HOME = LEGACY_PACKAGE_ROOT / "runtime" / "pyvideotrans"
LEGACY_TOOLS = LEGACY_PACKAGE_ROOT / "tools"
OS_NAME = platform.system().lower()
IS_WINDOWS = OS_NAME == "windows"
IS_LINUX = OS_NAME == "linux"
IS_MAC = OS_NAME == "darwin"
ARCH = platform.machine().lower()

# Legacy / external install used when the package has not been bootstrapped.
# The vendored runtime is always preferred; this is only a fallback.
DEFAULT_HOME = str(Path.home() / "pyvideotrans")
DEFAULT_ASR_MODEL = os.environ.get("TRANS_ASR_MODEL", "large-v3-turbo")
DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"

# What gets copied from an existing checkout into vendor/pyvideotrans.
COPY_ENTRIES = [
    "cli.py", "sp.py", "webui.py", "pyproject.toml", "uv.lock",
    ".python-version", "README.md", "LICENSE",
    "videotrans", "docs", "f5-tts", "ffmpeg",
]
COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", "*.log", ".pytest_cache",
)

# --------------------------------------------------------------------------
# Channel tables (indices mirror videotrans/translator/_constants.py and
# videotrans/tts/_constants.py; see references/channels.md for the full list).
# --------------------------------------------------------------------------
TRANSLATE_CHANNELS = {
    "google": 0, "microsoft": 1, "m2m100": 2, "hymt2": 3,
    "chatgpt": 4, "openai": 4, "deepseek": 5, "gemini": 6,
    "zhipuai": 7, "zhipu": 7, "azure": 8,
    "ollama": 9, "localllm": 9, "openai_compatible": 9, "relay": 9,
    "openrouter": 10, "siliconflow": 11, "ai302": 12, "bailian": 13,
    "volcengine": 14, "zijie": 14, "xiaomi": 15, "minimax": 16,
    "camb": 17, "deepl": 18, "deeplx": 19, "baidu": 20, "ali": 21,
    "libre": 22, "tencent": 23, "transapi": 24, "litellm": 25,
    "api_route": 26, "cheaperinference": 27, "infistar": 28, "atlascloud": 29,
}

CANONICAL = {
    0: "google", 1: "microsoft", 2: "m2m100", 3: "hymt2",
    4: "chatgpt", 5: "deepseek", 6: "gemini", 7: "zhipuai", 8: "azure",
    9: "localllm", 10: "openrouter", 11: "siliconflow", 12: "ai302",
    13: "bailian", 14: "volcengine", 15: "xiaomi", 16: "minimax",
    17: "camb", 18: "deepl", 19: "deeplx", 20: "baidu", 21: "ali",
    22: "libre", 23: "tencent", 24: "transapi", 25: "litellm",
    26: "api_route", 27: "cheaperinference", 28: "infistar", 29: "atlascloud",
}

# key/model/api are params.json field names.
CHANNEL_CONFIG = {
    "chatgpt":        {"key": "chatgpt_key", "model": "chatgpt_model", "api": "chatgpt_api", "env": "OPENAI_API_KEY"},
    "deepseek":       {"key": "deepseek_key", "model": "deepseek_model", "env": "DEEPSEEK_API_KEY"},
    "gemini":         {"key": "gemini_key", "model": "gemini_model", "env": "GEMINI_API_KEY"},
    "zhipuai":        {"key": "zhipuai_key", "model": "zhipuai_model", "env": "ZHIPUAI_API_KEY"},
    "azure":          {"key": "azure_key", "model": "azure_model", "api": "azure_api", "env": "AZURE_OPENAI_API_KEY"},
    "ollama":         {"key": "localllm_key", "model": "localllm_model", "api": "localllm_api", "env": None},
    "localllm":       {"key": "localllm_key", "model": "localllm_model", "api": "localllm_api", "env": None},
    "openai_compatible": {"key": "localllm_key", "model": "localllm_model", "api": "localllm_api", "env": "OPENAI_COMPATIBLE_API_KEY"},
    "relay":          {"key": "localllm_key", "model": "localllm_model", "api": "localllm_api", "env": "OPENAI_COMPATIBLE_API_KEY"},
    "openrouter":     {"key": "openrouter_key", "model": "openrouter_model", "env": "OPENROUTER_API_KEY"},
    "siliconflow":    {"key": "siliconflow_key", "model": "siliconflow_model", "env": "SILICONFLOW_API_KEY"},
    "ai302":          {"key": "ai302_key", "model": "ai302_model", "env": "AI302_API_KEY"},
    "bailian":        {"key": "bailian_key", "model": "bailian_model", "env": "DASHSCOPE_API_KEY"},
    "volcengine":     {"key": "zijiehuoshan_key", "model": "zijiehuoshan_model", "env": "VOLCENGINE_API_KEY"},
    "xiaomi":         {"key": "xiaomi_key", "model": "xiaomi_model", "env": "XIAOMI_API_KEY"},
    "minimax":        {"key": "minimaxi_key", "model": "minimaxi_text_model", "api": "minimaxi_api", "env": "MINIMAX_API_KEY"},
    "litellm":        {"key": "litellm_key", "model": "litellm_model", "api": "litellm_api", "env": "LITELLM_API_KEY"},
    "api_route":      {"key": "api_route_key", "model": "api_route_model", "env": "API_ROUTE_API_KEY"},
    "infistar":       {"key": "infistar_key", "model": "infistar_model", "env": "INFISTAR_API_KEY"},
    "atlascloud":     {"key": "atlascloud_key", "model": "atlascloud_model", "env": "ATLASCLOUD_API_KEY"},
    "cheaperinference": {"key": "cheaperinference_key", "model": "cheaperinference_model", "env": "CHEAPERINFERENCE_API_KEY"},
    "deepl":          {"key": "deepl_key", "api": "deepl_api", "env": "DEEPL_API_KEY"},
    "deeplx":         {"key": "deeplx_key", "api": "deeplx_api", "env": None},
    "baidu":          {"key": "baidu_key", "env": "BAIDU_API_KEY"},
    "ali":            {"key": "ali_key", "env": "DASHSCOPE_API_KEY"},
    "libre":          {"key": "libre_key", "api": "libre_api", "env": None},
    "tencent":        {"key": "tencent_key", "env": "TENCENT_API_KEY"},
    "transapi":       {"key": "transapi_key", "api": "transapi_url", "env": None},
}

TTS_CHANNELS = {
    "edge": 0, "qwen3local": 1, "f5": 2, "omnivoice": 3, "confucius": 4,
    "moss": 5, "zipvoice": 6, "piper": 7, "chatterbox": 8, "supertonic": 9,
    "vitscn": 10, "higgs": 11, "index": 12, "gptsovits": 13, "cosyvoice": 14,
    "voxcpm": 15, "firered": 16, "doubao": 17, "bailian": 18, "xiaomi": 19,
    "glm": 20, "minimax": 21, "openai": 22, "gemini": 23, "elevenlabs": 24,
    "audiocpp": 25, "chattts": 26, "spark": 27, "kokoro": 28, "fish": 29,
    "xai": 30, "gtts": 31, "azure": 32, "ai302": 33, "camb": 34,
    "tts_api": 35, "siliconflow": 36, "openrouter": 37, "deepgram": 38,
    "clone": 39, "fishaudio": 40,
}

# Default Edge-TTS voice per target language.
DEFAULT_VOICES = {
    "en": "en-US-GuyNeural",
    "zh-cn": "zh-CN-XiaoxiaoNeural",
    "zh-tw": "zh-TW-HsiaoChenNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "es": "es-ES-ElviraNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "it": "it-IT-ElsaNeural",
    "ar": "ar-EG-SalmaNeural",
    "hi": "hi-IN-SwaraNeural",
    "th": "th-TH-PremwadeeNeural",
    "vi": "vi-VN-HoaiMyNeural",
    "id": "id-ID-GadisNeural",
    "tr": "tr-TR-EmelNeural",
    "nl": "nl-NL-FennaNeural",
    "pl": "pl-PL-ZofiaNeural",
    "uk": "uk-UA-PolinaNeural",
    "ms": "ms-MY-YasminNeural",
}

# OpenAI-compatible base URLs used only by `test-channel`.
TEST_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "chatgpt": "https://api.openai.com/v1",
    "ollama": "http://127.0.0.1:11434/v1",
    "localllm": "http://127.0.0.1:11434/v1",
    "openai_compatible": "http://127.0.0.1:11434/v1",
    "relay": "http://127.0.0.1:11434/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "zhipuai": "https://open.bigmodel.cn/api/paas/v4",
    "bailian": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_route": "https://www.api-route.com/v1",
}

TASKS = ("vtv", "stt", "sts", "tts")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _safe_write(text: str, stream) -> None:
    """Print even when the console code page (e.g. GBK) cannot encode a character."""
    try:
        print(text, file=stream, flush=True)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"),
              file=stream, flush=True)


def fail(msg: str, code: int = 2) -> NoReturn:
    _safe_write(f"ERROR: {msg}", sys.stderr)
    raise SystemExit(code)


def log(msg: str = ""):
    _safe_write(msg, sys.stdout)


def redact(value) -> str:
    if not value:
        return "(not set)"
    s = str(value)
    return "(set)" if len(s) <= 8 else f"(set, ****{s[-4:]})"


def _is_program(path: Path) -> bool:
    return (path / "cli.py").is_file()


# --------------------------------------------------------------------------
# Layout migration (pre-1.3 package root -> skill-local vendor/tools)
# --------------------------------------------------------------------------
def _same_path(a: Path | None, b: Path | None) -> bool:
    if a is None or b is None:
        return False
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child = child.resolve()
        parent = parent.resolve()
    except OSError:
        return False
    return child != parent and parent in child.parents


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        return bool(isjunction and isjunction(path))
    except OSError:
        return False


def _looks_like_runtime_state(path: Path | None) -> bool:
    if path is None or not path.is_dir():
        return False
    return any((path / name).exists() for name in (
        "cli.py", "pyproject.toml", "videotrans", "models",
        "logs", "tmp", "output", ".venv"))


def _launcher_mentions_this_skill(path: Path) -> bool:
    try:
        return path.is_file() and "trans_cli.py" in path.read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return False


def _assert_migration_paths_safe(legacy_home: Path | None,
                                 legacy_tools: Path | None) -> None:
    for label, path in (("legacy runtime", legacy_home),
                        ("legacy tools", legacy_tools)):
        if path is not None and _is_link(path):
            fail(
                f"refusing to migrate through a symlink/junction ({label}): {path}\n"
                f"Pass --migrate-from with the real directory path."
            )
    if legacy_home is not None:
        if _same_path(legacy_home, BUNDLED_HOME):
            fail(f"--migrate-from points at the skill runtime itself: {legacy_home}")
        if _is_within(SKILL_DIR, legacy_home) or _is_within(legacy_home, SKILL_DIR):
            fail(
                f"refusing to migrate {legacy_home}: it overlaps the skill "
                f"directory {SKILL_DIR}."
            )
    if legacy_tools is not None:
        if _same_path(legacy_tools, TOOLS_DIR):
            fail(f"--migrate-from points at the skill tools directory itself: {legacy_tools}")
        if _is_within(TOOLS_DIR, legacy_tools) or _is_within(legacy_tools, TOOLS_DIR):
            fail(
                f"refusing to migrate {legacy_tools}: it overlaps the skill "
                f"tools directory {TOOLS_DIR}."
            )


def _detect_legacy(args) -> tuple[Path | None, Path | None]:
    """Find pre-1.3 runtime state. Returns (legacy_home, legacy_tools)."""
    explicit = getattr(args, "migrate_from", None)
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not root.is_dir():
            fail(f"--migrate-from {root}: directory not found")
        nested = root / "runtime" / "pyvideotrans"
        if _looks_like_runtime_state(nested):
            home = nested
        elif _looks_like_runtime_state(root):
            home = root
        else:
            home = None
        if nested.is_dir() or (root / "tools").is_dir():
            tools = root / "tools"
        elif home is not None and home.parent.name == "runtime":
            tools = home.parent.parent / "tools"
        else:
            tools = None
        if tools is not None and not tools.is_dir():
            tools = None
        if home is None and tools is None:
            fail(
                f"--migrate-from {root}: no legacy runtime state found "
                f"(expected {root}/runtime/pyvideotrans or a pyvideotrans tree)."
            )
        _assert_migration_paths_safe(home, tools)
        return home, tools

    home = LEGACY_HOME
    home_ok = _looks_like_runtime_state(home)
    tools_ok = LEGACY_TOOLS.is_dir()
    if not home_ok and not tools_ok:
        return None, None
    if _same_path(home, BUNDLED_HOME):
        return None, None
    # A global install puts parent^3 at the user's home, where an unrelated
    # ~/runtime/pyvideotrans could exist. Only auto-migrate when that
    # directory is recognizably this package (its launchers forward to us).
    if not (_launcher_mentions_this_skill(LEGACY_PACKAGE_ROOT / "trans.cmd")
            or _launcher_mentions_this_skill(LEGACY_PACKAGE_ROOT / "trans")):
        log(
            f"note: ignoring possible legacy runtime at "
            f"{home if home_ok else LEGACY_TOOLS}: {LEGACY_PACKAGE_ROOT} is not "
            f"this package's root (use --migrate-from <dir> to migrate explicitly)."
        )
        return None, None
    _assert_migration_paths_safe(home if home_ok else None,
                                 LEGACY_TOOLS if tools_ok else None)
    return (home if home_ok else None), (LEGACY_TOOLS if tools_ok else None)


def _ensure_writable(path: Path) -> None:
    probe = path / ".trans_write_probe"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        fail(
            f"the skill directory is not writable: {path} ({exc}).\n"
            f"Copy the skill to a writable location (e.g. ~/.claude/skills/) and retry."
        )


def _merge_move_file(src: Path, dst: Path) -> bool:
    """Move one file; the destination wins. Never deletes the source here."""
    if not src.is_file() or dst.exists():
        return False
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    except OSError as exc:
        fail(
            f"migration failed for {src} -> {dst}: {exc}\n"
            f"Close any running trans/uv/Ollama processes and retry.",
            code=1,
        )
    return True


def _merge_move_dir(src: Path, dst: Path, label: str, fatal: bool = True) -> None:
    """Recursively move entries missing at dst; existing destination entries win."""
    if not src.is_dir():
        return
    try:
        dst.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        if fatal:
            fail(f"cannot create {dst}: {exc}", code=1)
        log(f"WARNING: cannot create {dst}: {exc}")
        return
    for child in list(src.iterdir()):
        target = dst / child.name
        try:
            if not target.exists():
                shutil.move(str(child), str(target))
            elif child.is_dir() and target.is_dir():
                _merge_move_dir(child, target, f"{label}/{child.name}", fatal)
            else:
                log(f"WARNING: {target} already exists; keeping it and leaving "
                    f"{child} in place")
        except OSError as exc:
            message = f"migration failed for {child} -> {target}: {exc}"
            if fatal:
                fail(message + "\nClose any running trans/uv/Ollama processes "
                               "and retry.", code=1)
            log(f"WARNING: {message}")


def _merge_params_file(src: Path, dst: Path) -> int:
    """Merge legacy params into the destination, filling only missing/empty keys."""
    if not src.is_file():
        return 0
    if not dst.is_file():
        _merge_move_file(src, dst)
        return 0
    try:
        legacy = json.loads(src.read_text(encoding="utf-8"))
        current = json.loads(dst.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"cannot merge {src} into {dst}: {exc}; legacy file kept intact",
             code=1)
    if not isinstance(legacy, dict) or not isinstance(current, dict):
        fail(f"cannot merge {src} into {dst}: unexpected JSON shape", code=1)
    filled = 0
    for key, value in legacy.items():
        if value in ("", None):
            continue
        if key not in current or current[key] in ("", None):
            current[key] = value
            filled += 1
    if filled:
        tmp = dst.with_name(dst.name + ".migrate.tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, dst)
    return filled


def _migrate_tools(legacy_tools: Path | None) -> None:
    if not legacy_tools or not legacy_tools.is_dir():
        return
    if _same_path(legacy_tools, TOOLS_DIR):
        return
    log(f"migrating tools: {legacy_tools} -> {TOOLS_DIR}")
    _merge_move_dir(legacy_tools, TOOLS_DIR, "tools")


def _migrate_state(legacy_home: Path | None, runtime: Path) -> None:
    if not legacy_home or not legacy_home.is_dir():
        return
    if _same_path(legacy_home, runtime):
        return
    runtime.mkdir(parents=True, exist_ok=True)
    log(f"migrating runtime state: {legacy_home} -> {runtime}")

    filled = _merge_params_file(
        legacy_home / "videotrans" / "params.json",
        runtime / "videotrans" / "params.json",
    )
    if filled:
        log(f"params.json: filled {filled} missing/empty value(s) from legacy config")

    for rel in ("videotrans/cfg.json", "videotrans/codec.json",
                "videotrans/languages.json",
                "videotrans/voicejson/camb_languages.json"):
        _merge_move_file(legacy_home / rel, runtime / rel)

    for name in ("models", "output"):
        _merge_move_dir(legacy_home / name, runtime / name, name)
    _merge_move_dir(legacy_home / "logs", runtime / "logs", "logs", fatal=False)


def _rmdir_if_empty(path: Path) -> bool:
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
            return True
    except OSError as exc:
        log(f"WARNING: could not remove empty directory {path}: {exc}")
    return False


def _remove_tree(path: Path, label: str) -> bool:
    if not path.exists():
        return True

    def _onerror(func, p, exc_info):  # noqa: ANN001
        try:
            os.chmod(p, os.stat(p).st_mode | 0o200)
            func(p)
        except OSError:
            pass

    for attempt in range(3):
        try:
            shutil.rmtree(path, onerror=_onerror)
            return True
        except OSError as exc:
            if attempt == 2:
                log(f"WARNING: could not remove {label} {path}: {exc}")
                return False
            time.sleep(0.5)
    return False


def _cleanup_legacy(legacy_home: Path | None, legacy_tools: Path | None,
                    runtime: Path, sync_ok: bool) -> None:
    """Remove migrated legacy state. Only runs after the new venv is ready."""
    if not sync_ok:
        if legacy_home or legacy_tools:
            log("legacy cleanup deferred until the skill venv is ready "
                "(old state kept)")
        return
    if legacy_home and legacy_home.is_dir() and not _same_path(legacy_home, runtime):
        log(f"cleaning legacy runtime: {legacy_home}")
        for name in ("models", "logs", "output"):
            path = legacy_home / name
            if path.is_dir() and any(path.iterdir()):
                log(f"WARNING: keeping {path}: it still contains unmigrated files")
            else:
                _rmdir_if_empty(path)
        _remove_tree(legacy_home / "tmp", "legacy temp dir")
        for rel in ("videotrans/params.json", "videotrans/params.json.bak",
                    "videotrans/params.json.tmp", "videotrans/cfg.json",
                    "videotrans/codec.json", "videotrans/languages.json",
                    "videotrans/voicejson/camb_languages.json"):
            path = legacy_home / rel
            if path.is_file():
                log(f"WARNING: keeping {path}: it was not migrated")
        _remove_tree(legacy_home / ".venv", "legacy venv")
        _rmdir_if_empty(legacy_home)
        if legacy_home.parent.name == "runtime":
            _rmdir_if_empty(legacy_home.parent)
    if (legacy_tools and legacy_tools.is_dir()
            and not _same_path(legacy_tools, TOOLS_DIR)):
        if any(legacy_tools.iterdir()):
            log(f"WARNING: keeping {legacy_tools}: it still contains unmigrated files")
        else:
            _rmdir_if_empty(legacy_tools)


def latest_log(home: Path) -> Path | None:
    for logs in (home / "logs", home / "videotrans" / "logs"):
        if logs.is_dir():
            recent = sorted(logs.glob("*.log"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
            if recent:
                return recent[0]
    return None


def resolve_home_info(args) -> tuple[Path, str]:
    """Resolve the pyVideoTrans checkout and say where it came from.

    Order: --home > PYVIDEOTRANS_HOME > vendored runtime > external default.
    The vendored runtime is what makes this skill portable: once `setup` has
    run, no external pyvideotrans installation is needed.
    """
    explicit = getattr(args, "home", None)
    if explicit:
        home = Path(explicit).expanduser().resolve()
        if not _is_program(home):
            fail(f"pyVideoTrans not found at {home} (no cli.py)")
        return home, "flag"

    env = os.environ.get("PYVIDEOTRANS_HOME")
    if env:
        home = Path(env).expanduser().resolve()
        if not _is_program(home):
            fail(f"PYVIDEOTRANS_HOME points to {home}, but it has no cli.py")
        return home, "env"

    if _is_program(BUNDLED_HOME):
        return BUNDLED_HOME, "bundled"

    external = Path(DEFAULT_HOME).expanduser().resolve()
    if _is_program(external):
        return external, "external"

    fail(
        "no pyVideoTrans installation found.\n"
        "  - Run `trans_cli.py setup` to create the vendored runtime, or\n"
        "  - set PYVIDEOTRANS_HOME / pass --home <dir> (must contain cli.py)."
    )


def resolve_home(args) -> Path:
    return resolve_home_info(args)[0]


def _dir_size_mb(path: Path) -> float:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total / 1024 / 1024


def _git_proxy() -> str | None:
    try:
        out = subprocess.run(
            ["git", "config", "--global", "--get", "http.proxy"],
            capture_output=True, text=True, timeout=15,
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _redact_params_file(path: Path) -> int:
    """Blank every *_key field so the portable package carries no secrets."""
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    changed = 0
    for key, value in list(data.items()):
        if key.endswith("_key") and isinstance(value, str) and value:
            data[key] = ""
            changed += 1
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def _source_size_mb(root: Path) -> float:
    total = 0.0
    for entry in COPY_ENTRIES:
        p = root / entry
        if p.is_file():
            total += p.stat().st_size / 1024 / 1024
        elif p.is_dir():
            total += _dir_size_mb(p)
    return total


def _sanitize_cfg_file(path: Path, runtime: Path) -> None:
    """Make cfg.json portable: no tokens, no machine-specific paths/proxy."""
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    data["hf_token"] = ""
    data["proxy"] = ""
    data["homedir"] = str(runtime / "output").replace("\\", "/")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _copy_runtime(source: Path, dest: Path, update: bool) -> bool:
    if _same_path(source, dest):
        log("source and destination are the same; skipping copy")
        return False
    if _is_within(dest, source):
        fail(f"refusing to copy {source} into itself ({dest})")
    dest.mkdir(parents=True, exist_ok=True)
    existing_cfg = dest / "videotrans" / "params.json"
    cfg_backup = existing_cfg.read_bytes() if update and existing_cfg.is_file() else None

    for entry in COPY_ENTRIES:
        src = source / entry
        dst = dest / entry
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dst, ignore=COPY_IGNORE, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

    if cfg_backup is not None:
        existing_cfg.write_bytes(cfg_backup)
    else:
        _redact_params_file(existing_cfg)

    # State that must not travel: backups and machine-specific config.
    bak = dest / "videotrans" / "params.json.bak"
    if bak.is_file():
        bak.unlink()
    _sanitize_cfg_file(dest / "videotrans" / "cfg.json", dest)
    return True


def uv_bin() -> str:
    uv = os.environ.get("UV_BIN") or shutil.which("uv")
    if not uv:
        fail("`uv` was not found on PATH. Install uv (https://docs.astral.sh/uv/) or set UV_BIN.")
    return uv


def params_path(home: Path) -> Path:
    return home / "videotrans" / "params.json"


def ensure_params(home: Path) -> None:
    """Create params.json with upstream defaults when it is missing.

    params.json is generated state that may contain API keys, so a fresh
    checkout does not ship it. Importing AppParams writes the default file;
    this keeps `check` / `vtv` usable immediately after `setup`.
    """
    if params_path(home).is_file():
        return
    uv = os.environ.get("UV_BIN") or shutil.which("uv")
    if not uv:
        bundled = TOOLS_DIR / ("uv.exe" if IS_WINDOWS else "uv")
        uv = str(bundled) if bundled.is_file() else None
    if not uv:
        return
    script = "from videotrans.configure._app_params import AppParams; AppParams()"
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    try:
        subprocess.run([uv, "run", "--no-sync", "python", "-c", script],
                       cwd=str(home), env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=120)
    except Exception:  # noqa: BLE001
        pass


def load_params(home: Path) -> dict:
    p = params_path(home)
    if not p.is_file():
        ensure_params(home)
    if not p.is_file():
        fail(f"params.json not found: {p}. Run `setup` first, then retry.")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"cannot parse {p}: {exc}")


def save_params(home: Path, params: dict) -> None:
    p = params_path(home)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def resolve_translate(name) -> tuple[int, str]:
    if name is None:
        return -1, ""
    s = str(name).strip().lower().replace("-", "_")
    if s.isdigit():
        idx = int(s)
        if idx not in CANONICAL:
            fail(f"unknown translation channel index {idx}. Run `channels` to list valid ones.")
        return idx, CANONICAL[idx]
    if s not in TRANSLATE_CHANNELS:
        valid = ", ".join(sorted(set(TRANSLATE_CHANNELS)))
        fail(f"unknown translation channel '{name}'.\nValid names: {valid}")
    return TRANSLATE_CHANNELS[s], s


def resolve_tts(name) -> tuple[int, str]:
    if name is None:
        return -1, ""
    s = str(name).strip().lower().replace("-", "_")
    if s.isdigit():
        idx = int(s)
        reverse = {v: k for k, v in TTS_CHANNELS.items()}
        if idx not in reverse:
            fail(f"unknown TTS channel index {idx}. Run `channels` to list valid ones.")
        return idx, reverse[idx]
    if s not in TTS_CHANNELS:
        valid = ", ".join(sorted(TTS_CHANNELS))
        fail(f"unknown TTS channel '{name}'.\nValid names: {valid}")
    return TTS_CHANNELS[s], s


def configure_channel(home: Path, params: dict, channel_name: str, args) -> dict:
    """Write channel credentials/model/base-url into params.json.

    Returns the dict of fields that were changed (values are never printed).
    """
    cfg = CHANNEL_CONFIG.get(channel_name)
    if not cfg:
        return {}

    changes: dict = {}
    key_field = cfg.get("key")
    model_field = cfg.get("model")
    api_field = cfg.get("api")

    # API key: explicit flag > environment > existing config.
    new_key = args.api_key
    if not new_key and cfg.get("env"):
        new_key = os.environ.get(cfg["env"])
    if not new_key and channel_name in ("ollama", "localllm", "openai_compatible", "relay"):
        # The OpenAI SDK refuses an empty key even though Ollama ignores it.
        new_key = params.get("localllm_key") or "ollama"
    if new_key and key_field:
        changes[key_field] = new_key

    # Model.
    if args.model and model_field:
        changes[model_field] = args.model
    elif channel_name in ("ollama", "localllm") and model_field and not params.get(model_field):
        changes[model_field] = "qwen2.5:3b"

    # Base URL.
    if args.api_base and api_field:
        changes[api_field] = args.api_base
    elif channel_name in ("ollama", "localllm") and api_field and not params.get(api_field):
        changes[api_field] = "http://127.0.0.1:11434/v1"

    if changes:
        params.update(changes)
        save_params(home, params)
    return changes


# --------------------------------------------------------------------------
# Output directory safety
# --------------------------------------------------------------------------
def resolve_outdir(home: Path, input_path: Path, outdir: str | None, overwrite: bool) -> Path:
    explicit = bool(outdir)
    if explicit:
        out = Path(outdir).expanduser().resolve()
        if out.exists() and any(out.iterdir()) and not overwrite:
            fail(
                f"output directory is not empty: {out}\n"
                f"pyVideoTrans deletes --output-dir recursively at the start of every task, so "
                f"the wrapper refuses to aim it at a non-empty folder.\n"
                f"Pass --overwrite to allow deleting its contents, or choose another --outdir."
            )
    else:
        base = input_path.parent / f"{input_path.stem}_translated"
        out = base
        n = 2
        while out.exists() and any(out.iterdir()):
            out = base.with_name(f"{base.name}-{n}")
            n += 1

    inp = input_path.resolve()
    if out == inp or out in inp.parents:
        fail(
            f"refusing to use {out} as output directory: it contains the input file "
            f"({inp}), and pyVideoTrans would delete it. Choose a different --outdir."
        )
    dangerous = {Path.home().resolve(), Path(out.anchor).resolve(), home.resolve()}
    if out in dangerous:
        fail(f"refusing to use {out} as output directory: too broad / would delete important data.")

    skill = SKILL_DIR.resolve()
    if out == skill or out in skill.parents or skill in out.parents:
        fail(
            f"refusing to use {out} as output directory: it overlaps the skill "
            f"installation ({skill}), and pyVideoTrans would delete it. "
            f"Choose a different --outdir."
        )

    out.mkdir(parents=True, exist_ok=True)
    return out


def snapshot(root: Path) -> dict[str, tuple[int, float]]:
    if not root.exists():
        return {}
    return {
        str(p): (p.stat().st_size, p.stat().st_mtime)
        for p in root.rglob("*")
        if p.is_file()
    }


def changed_files(before: dict, after: dict) -> list[Path]:
    out = []
    for path, meta in after.items():
        if before.get(path) != meta:
            out.append(Path(path))
    return sorted(out)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Provisioning: detect the machine, reuse caches, download only what is missing
# --------------------------------------------------------------------------
MODEL_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "base": "Systran/faster-whisper-base",
    "base.en": "Systran/faster-whisper-base.en",
    "small": "Systran/faster-whisper-small",
    "small.en": "Systran/faster-whisper-small.en",
    "medium": "Systran/faster-whisper-medium",
    "medium.en": "Systran/faster-whisper-medium.en",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
    "distil-medium.en": "Systran/faster-distil-whisper-medium.en",
    "distil-small.en": "Systran/faster-distil-whisper-small.en",
}

OLLAMA_URL = "http://127.0.0.1:11434"


def proxy_url(explicit=None) -> str | None:
    return (explicit or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
            or _git_proxy())


def _urlopen(url: str, proxy: str | None = None, timeout: int = 60):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": f"trans-video-cli/{VERSION}"})
    return opener.open(req, timeout=timeout)


def download_file(url: str, dest: Path, proxy: str | None, label: str) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    log(f"downloading {label} ...")
    with _urlopen(url, proxy, timeout=120) as resp, open(part, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        mark = 0
        while True:
            chunk = resp.read(1024 * 512)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total and done - mark >= 20 * 1024 * 1024:
                mark = done
                log(f"  {label}: {done * 100 // total}% ({done / 1048576:.0f} MB)")
    part.replace(dest)
    log(f"  {label}: saved {dest.name} ({dest.stat().st_size / 1048576:.1f} MB)")
    return dest


def _safe_extract_zip(archive: Path, dest: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        base = dest.resolve()
        for name in zf.namelist():
            if not str((dest / name).resolve()).startswith(str(base)):
                fail(f"unsafe path in archive: {name}")
        zf.extractall(dest)


def _safe_extract_tar(archive: Path, dest: Path) -> None:
    with tarfile.open(archive) as tf:
        base = dest.resolve()
        for member in tf.getmembers():
            if not str((dest / member.name).resolve()).startswith(str(base)):
                fail(f"unsafe path in archive: {member.name}")
        tf.extractall(dest)


def _extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip":
        _safe_extract_zip(archive, dest)
    elif archive.name.lower().endswith((".tar.gz", ".tgz", ".tar.xz")):
        _safe_extract_tar(archive, dest)
    else:
        fail(f"unsupported archive format: {archive.name}")


def _find_one(root: Path, name: str) -> Path | None:
    for p in root.rglob(name):
        if p.is_file():
            return p
    return None


def detect_nvidia() -> dict | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15,
        )
        lines = (out.stdout or "").strip().splitlines()
        if out.returncode == 0 and lines:
            parts = [p.strip() for p in lines[0].split(",")]
            return {
                "name": parts[0],
                "memory": parts[1] if len(parts) > 1 else "",
                "driver": parts[2] if len(parts) > 2 else "",
            }
    except Exception:  # noqa: BLE001
        pass
    return None


def resolve_uv(auto: bool, proxy: str | None) -> str:
    uv = os.environ.get("UV_BIN") or shutil.which("uv")
    if uv:
        return uv
    bundled = TOOLS_DIR / ("uv.exe" if IS_WINDOWS else "uv")
    if bundled.is_file():
        return str(bundled)
    if not auto:
        fail("uv not found. Re-run with downloads enabled, install uv, or set UV_BIN.")

    log("uv not found - provisioning it into the package ...")
    if IS_WINDOWS:
        asset = "uv-x86_64-pc-windows-msvc.zip"
        archive = DOWNLOAD_CACHE / "uv-windows.zip"
    elif IS_LINUX:
        asset = "uv-x86_64-unknown-linux-gnu.tar.gz"
        archive = DOWNLOAD_CACHE / "uv-linux.tar.gz"
    elif IS_MAC:
        arch = "aarch64" if ARCH in ("arm64", "aarch64") else "x86_64"
        asset = f"uv-{arch}-apple-darwin.tar.gz"
        archive = DOWNLOAD_CACHE / f"uv-macos-{arch}.tar.gz"
    else:
        fail(f"unsupported platform for uv provisioning: {OS_NAME}")

    url = f"https://github.com/astral-sh/uv/releases/latest/download/{asset}"
    if not archive.is_file():
        download_file(url, archive, proxy, "uv")
    tmp = TOOLS_DIR / "_tmp_uv"
    shutil.rmtree(tmp, ignore_errors=True)
    _extract_archive(archive, tmp)
    exe = _find_one(tmp, "uv.exe" if IS_WINDOWS else "uv")
    if not exe:
        fail("uv archive did not contain the uv executable")
    shutil.copy2(exe, bundled)
    shutil.rmtree(tmp, ignore_errors=True)
    if not IS_WINDOWS:
        os.chmod(bundled, 0o755)
    return str(bundled)


def resolve_ffmpeg(auto: bool, proxy: str | None) -> tuple[str, str] | None:
    ff = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")
    fp = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe")
    if ff and fp:
        return ff, fp

    exe = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"
    probe = "ffprobe.exe" if IS_WINDOWS else "ffprobe"
    bin_dir = TOOLS_DIR / "ffmpeg" / "bin"
    if (bin_dir / exe).is_file() and (bin_dir / probe).is_file():
        return str(bin_dir / exe), str(bin_dir / probe)

    if not auto:
        return None

    if IS_MAC:
        # No official static build; use the system package manager when present.
        for pm, cmd in (("brew", ["brew", "install", "ffmpeg"]),
                        ("port", ["port", "install", "ffmpeg"])):
            if shutil.which(pm):
                log(f"ffmpeg not found - installing with {pm} ...")
                proc = subprocess.run(cmd)
                if proc.returncode == 0:
                    ff_now = shutil.which("ffmpeg")
                    fp_now = shutil.which("ffprobe")
                    if ff_now and fp_now:
                        return ff_now, fp_now
        fail("ffmpeg not found. Install it with `brew install ffmpeg` "
             "(or MacPorts: `port install ffmpeg`), then re-run setup.")

    log("ffmpeg not found - provisioning it into the package ...")
    if IS_WINDOWS:
        url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        archive = DOWNLOAD_CACHE / "ffmpeg-windows-essentials.zip"
    else:
        url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
        archive = DOWNLOAD_CACHE / "ffmpeg-linux-static.tar.xz"
    if not archive.is_file():
        download_file(url, archive, proxy, "ffmpeg")

    tmp = TOOLS_DIR / "_tmp_ffmpeg"
    shutil.rmtree(tmp, ignore_errors=True)
    _extract_archive(archive, tmp)
    found_ff = _find_one(tmp, exe)
    found_fp = _find_one(tmp, probe)
    if not found_ff or not found_fp:
        fail("ffmpeg archive did not contain ffmpeg/ffprobe")
    bin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(found_ff, bin_dir / exe)
    shutil.copy2(found_fp, bin_dir / probe)
    shutil.rmtree(tmp, ignore_errors=True)
    if not IS_WINDOWS:
        os.chmod(bin_dir / exe, 0o755)
        os.chmod(bin_dir / probe, 0o755)
    return str(bin_dir / exe), str(bin_dir / probe)


def resolve_ollama_binary() -> str | None:
    cand = os.environ.get("OLLAMA_BIN") or shutil.which("ollama")
    if cand:
        return cand
    bundled = TOOLS_DIR / "ollama" / ("ollama.exe" if IS_WINDOWS else "ollama")
    return str(bundled) if bundled.is_file() else None


def provision_ollama(proxy: str | None, auto: bool = True) -> str:
    existing = resolve_ollama_binary()
    if existing:
        log(f"ollama: using {existing}")
        return existing
    if not auto:
        fail("Ollama not found and downloads are disabled. Install it or drop --no-download.")

    log("ollama not found - downloading the portable build ...")
    if IS_WINDOWS:
        url = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip"
        archive = DOWNLOAD_CACHE / "ollama-windows.zip"
        exe_name = "ollama.exe"
    elif IS_LINUX:
        url = "https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64.tgz"
        archive = DOWNLOAD_CACHE / "ollama-linux-amd64.tgz"
        exe_name = "ollama"
    else:
        fail("Ollama auto-provisioning supports Windows/Linux. "
             "On macOS install it from https://ollama.com/download, then re-run.")

    if not archive.is_file():
        download_file(url, archive, proxy, "ollama")
    dest = TOOLS_DIR / "ollama"
    shutil.rmtree(dest, ignore_errors=True)
    _extract_archive(archive, dest)
    exe = _find_one(dest, exe_name)
    if not exe:
        fail("ollama archive did not contain the ollama executable")
    if exe.parent != dest:
        for item in list(exe.parent.iterdir()):
            target = dest / item.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink()
            shutil.move(str(item), str(target))
        shutil.rmtree(exe.parent, ignore_errors=True)
        exe = dest / exe_name
    if not IS_WINDOWS:
        os.chmod(exe, 0o755)
    return str(exe)


def ollama_api_ready(timeout: int = 2) -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


def ollama_has_model(model: str) -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        names = [m.get("name", "") for m in data.get("models", [])]
        return model in names or f"{model}:latest" in names
    except Exception:  # noqa: BLE001
        return False


def ollama_start(binary: str) -> None:
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [binary, "serve"], cwd=str(Path(binary).parent),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs,
    )


def ensure_ollama_ready(model: str, auto: bool = True) -> None:
    if not ollama_api_ready():
        binary = resolve_ollama_binary()
        if not binary:
            fail(
                "Ollama is required by the local translation channel but is not installed.\n"
                "Run `setup --with-ollama` to provision it, or install it from https://ollama.com."
            )
        log(f"starting Ollama: {binary}")
        ollama_start(binary)
        for _ in range(30):
            time.sleep(1)
            if ollama_api_ready():
                break
        else:
            fail("Ollama did not start within 30s. Start it manually with `ollama serve`.")

    if not ollama_has_model(model):
        if not auto:
            fail(f"Ollama model '{model}' is not installed and downloads are disabled.")
        log(f"Ollama model '{model}' not found - pulling it (this may take a while) ...")
        payload = json.dumps({"name": model, "stream": False}).encode("utf-8")
        req = urllib.request.Request(
            OLLAMA_URL + "/api/pull", data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=3600) as resp:
                resp.read()
        except Exception as exc:  # noqa: BLE001
            fail(f"failed to pull Ollama model '{model}': {exc}")
        log(f"model '{model}' ready")


def _torch_block(pkg: str, variant: str) -> str:
    if variant == "cu128":
        return (
            f'{pkg} = [\n'
            f'    {{ index = "pytorch-cu128", marker = "sys_platform == \'win32\' or sys_platform == \'linux\'" }},\n'
            f'    {{ index = "pytorch-cpu", marker = "sys_platform == \'darwin\'" }},\n'
            f']'
        )
    return (
        f'{pkg} = [\n'
        f'    {{ index = "pytorch-cpu", marker = "sys_platform == \'win32\' or sys_platform == \'linux\' or sys_platform == \'darwin\'" }},\n'
        f']'
    )


def _set_torch_index(pyproject: Path, variant: str) -> bool:
    """Pin torch/torchaudio to the requested build; leave other sources untouched."""
    if not pyproject.is_file():
        return False
    text = pyproject.read_text(encoding="utf-8")
    changed = False
    for pkg in ("torch", "torchaudio"):
        pattern = re.compile(rf'({pkg} = \[\n)(.*?)(\n\])', re.S)
        new_block = _torch_block(pkg, variant)

        def repl(match, block=new_block):
            nonlocal changed
            old = match.group(1) + match.group(2) + match.group(3)
            if old != block:
                changed = True
            return block

        text = pattern.sub(repl, text, count=1)
    if changed:
        pyproject.write_text(text, encoding="utf-8")
    return changed


def _platform_has_pynini_wheel() -> bool:
    """pynini ships wheels for Windows (custom build) and Linux x86_64 only."""
    if IS_WINDOWS:
        return True
    if IS_LINUX and ARCH in ("x86_64", "amd64"):
        return True
    return False


def _drop_dependency(pyproject: Path, names: tuple[str, ...]) -> list[str]:
    """Remove simple quoted dependency lines such as `"pynini",`."""
    if not pyproject.is_file():
        return []
    text = pyproject.read_text(encoding="utf-8")
    removed: list[str] = []
    for name in names:
        pattern = re.compile(rf'^\s*"{re.escape(name)}(?:[=<>!~][^"]*)?"\s*,\s*$\n', re.M)
        text, count = pattern.subn("", text)
        if count:
            removed.append(name)
    if removed:
        pyproject.write_text(text, encoding="utf-8")
    return removed


def _drop_source_block(pyproject: Path, name: str) -> bool:
    if not pyproject.is_file():
        return False
    text = pyproject.read_text(encoding="utf-8")
    pattern = re.compile(rf'^{re.escape(name)} = \[\n.*?\n\]\n', re.M | re.S)
    new_text, count = pattern.subn("", text)
    if count:
        pyproject.write_text(new_text, encoding="utf-8")
    return bool(count)


def model_cache_dirs() -> list[Path]:
    dirs: list[Path] = []
    seen: set[str] = set()
    for base in (os.environ.get("PYVIDEOTRANS_HOME"), DEFAULT_HOME,
                 str(BUNDLED_HOME), str(LEGACY_HOME)):
        if not base:
            continue
        path = Path(base).expanduser() / "models"
        if path.is_dir() and str(path) not in seen:
            dirs.append(path)
            seen.add(str(path))
    return dirs


def _modelscope_download(home: Path, model_id: str, target: Path) -> int:
    """China-friendly source: ModelScope mirrors the faster-whisper repos directly."""
    env = os.environ.copy()
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                "https_proxy", "http_proxy", "all_proxy"):
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    env["PYTHONUTF8"] = "1"
    script = (
        "from modelscope import snapshot_download;"
        f"snapshot_download(model_id={model_id!r}, local_dir={str(target)!r})"
    )
    return subprocess.run([uv_bin(), "run", "python", "-c", script],
                          cwd=str(home), env=env).returncode


def _snapshot_download(home: Path, repo: str, target: Path,
                       endpoint: str, proxy: str | None) -> int:
    env = os.environ.copy()
    env["HF_ENDPOINT"] = endpoint
    env["PYTHONUTF8"] = "1"
    if proxy:
        env["HTTPS_PROXY"] = proxy
        env["HTTP_PROXY"] = proxy
        env["NO_PROXY"] = ",".join(
            x for x in (env.get("NO_PROXY", ""), "127.0.0.1", "localhost") if x
        )
    else:
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                    "https_proxy", "http_proxy", "all_proxy"):
            env.pop(key, None)
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"
    script = (
        "from huggingface_hub import snapshot_download;"
        f"snapshot_download(repo_id={repo!r}, local_dir={str(target)!r})"
    )
    proc = subprocess.run([uv_bin(), "run", "python", "-c", script], cwd=str(home), env=env)
    return proc.returncode


def _model_ready(target: Path) -> bool:
    """A directory counts as a usable cache only if the weights are actually there."""
    if not target.is_dir():
        return False
    return any((target / name).is_file()
               for name in ("model.bin", "model.safetensors", "config.json"))


def prefetch_model(home: Path, model: str, proxy: str | None) -> None:
    repo = MODEL_REPOS.get(model)
    if not repo:
        log(f"model {model}: unknown name, skipping (it will download on first use)")
        return
    target = home / "models" / f"models--{repo.replace('/', '--')}"
    if _model_ready(target):
        log(f"model {model}: using existing cache")
        return
    for cache in model_cache_dirs():
        src = cache / target.name
        if _model_ready(src):
            log(f"model {model}: copying local cache from {src}")
            shutil.copytree(src, target, dirs_exist_ok=True)
            return

    log(f"model {model}: downloading {repo} via ModelScope (direct) ...")
    if _modelscope_download(home, repo, target) == 0 and _model_ready(target):
        log(f"model {model}: ready")
        return
    log(f"model {model}: ModelScope failed, trying hf-mirror (direct) ...")
    if _snapshot_download(home, repo, target, "https://hf-mirror.com", None) == 0 and _model_ready(target):
        log(f"model {model}: ready")
        return
    if proxy:
        log(f"model {model}: mirror failed, retrying via huggingface.co + proxy ...")
        if _snapshot_download(home, repo, target, "https://huggingface.co", proxy) == 0 and _model_ready(target):
            log(f"model {model}: ready")
            return
    log(f"model {model}: download failed (it will retry on first use)")


def detect_environment() -> dict:
    nvidia = detect_nvidia()
    ff = resolve_ffmpeg(auto=False, proxy=None)
    uv = os.environ.get("UV_BIN") or shutil.which("uv")
    if not uv:
        bundled = TOOLS_DIR / ("uv.exe" if IS_WINDOWS else "uv")
        uv = str(bundled) if bundled.is_file() else "(missing)"
    return {
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "python": sys.version.split()[0],
        "uv": uv,
        "ffmpeg": ff[0] if ff else "(missing)",
        "ffprobe": ff[1] if ff else "(missing)",
        "nvidia_gpu": nvidia["name"] if nvidia else "(none detected)",
        "nvidia_driver": nvidia["driver"] if nvidia else "",
        "recommended_torch": "cu128" if nvidia else "cpu",
        "ollama": resolve_ollama_binary() or "(not installed)",
        "ollama_service": "running" if ollama_api_ready() else "stopped",
        "skill_dir": str(SKILL_DIR),
        "bundled_home": str(BUNDLED_HOME),
        "tools_dir": str(TOOLS_DIR),
    }


def cmd_setup(args) -> int:
    """Provision the skill: detect the machine, reuse caches, download what is missing."""
    proxy = proxy_url(args.proxy)
    auto = not args.no_download

    # 0) The skill directory must be writable: setup creates tools, the venv,
    #    models and params there.
    _ensure_writable(SKILL_DIR)

    # 1) migrate pre-1.3 state (tools first, so detection can see them)
    legacy_home, legacy_tools = _detect_legacy(args)
    if legacy_home:
        log(f"legacy runtime detected: {legacy_home}")
    if legacy_tools:
        log(f"legacy tools detected:   {legacy_tools}")
    _migrate_tools(legacy_tools)

    env_info = detect_environment()
    log("environment:")
    for key in ("os", "arch", "nvidia_gpu", "nvidia_driver",
                "recommended_torch", "uv", "ffmpeg", "ollama"):
        log(f"  {key:20} {env_info.get(key, '')}")

    # 2) uv - required to create the venv and to run cli.py
    uv = resolve_uv(auto=auto, proxy=proxy)
    log(f"uv: {uv}")

    # 3) ffmpeg / ffprobe - reuse PATH or a previous download; fetch if absent
    ff = resolve_ffmpeg(auto=auto, proxy=proxy)
    if ff:
        log(f"ffmpeg: {ff[0]}")
    else:
        log("WARNING: ffmpeg not found and downloads are disabled")

    # 4) use the vendored source shipped with the skill; copy only when it is
    #    missing or the user explicitly passes --copy-from.
    runtime = BUNDLED_HOME
    source = None
    if args.copy_from:
        source = Path(args.copy_from).expanduser().resolve()
        if not _is_program(source):
            fail(f"--copy-from {source} has no cli.py")
    elif not _is_program(runtime):
        candidates = []
        env_home = os.environ.get("PYVIDEOTRANS_HOME")
        if env_home:
            candidates.append(Path(env_home).expanduser().resolve())
        if legacy_home and _is_program(legacy_home):
            candidates.append(legacy_home)
        candidates.append(Path(DEFAULT_HOME).expanduser().resolve())
        source = next((c for c in candidates if _is_program(c)), None)
        if source is None:
            fail(
                "vendored pyVideoTrans source is missing: "
                f"{runtime} (no cli.py).\n"
                "Reinstall the skill directory, or pass --copy-from <dir> "
                "pointing at a pyVideoTrans checkout."
            )

    if source:
        log(f"copying source: {source}")
        log(f"            to: {runtime}")
        if _copy_runtime(source, runtime, update=runtime.exists()):
            log(f"source copied ({_source_size_mb(runtime):.1f} MB; "
                f"secrets blanked, cfg.json made portable)")
    else:
        log(f"vendored runtime: {runtime}")
        if args.force:
            log("--force: source is shipped with the skill; refreshing provisioning "
                "only (use --copy-from to replace it)")

    # 5) migrate runtime state after the source is in place, so a copy cannot
    #    overwrite migrated params. cfg.json is always re-sanitized because a
    #    copied skill may carry a stale absolute homedir.
    _migrate_state(legacy_home, runtime)
    _sanitize_cfg_file(runtime / "videotrans" / "cfg.json", runtime)

    # 6) pick the PyTorch build that matches this machine
    variant = args.torch
    if variant == "auto":
        variant = "cu128" if env_info["nvidia_gpu"] != "(none detected)" else "cpu"
    if _set_torch_index(runtime / "pyproject.toml", variant):
        log(f"torch variant: {variant} (pyproject updated; uv will re-resolve)")
    else:
        log(f"torch variant: {variant} (already configured)")

    # 7) pynini/WeTextProcessing only affect MOSS-TTS text normalisation and
    #    have no wheels on macOS / ARM Linux, so drop them where needed.
    if not (args.keep_pynini or _platform_has_pynini_wheel()):
        removed = _drop_dependency(runtime / "pyproject.toml",
                                   ("pynini", "WeTextProcessing"))
        _drop_source_block(runtime / "pyproject.toml", "pynini")
        if removed:
            log("platform without pynini wheels: dropped optional deps "
                f"{', '.join(removed)} (MOSS TTS text normalisation unavailable)")

    # 8) create the venv
    sync_ok = False
    if args.no_sync:
        log("skipping `uv sync` (--no-sync)")
        sync_ok = (runtime / ".venv").is_dir()
    else:
        env = os.environ.copy()
        index = args.index_url or os.environ.get("UV_DEFAULT_INDEX") or DEFAULT_INDEX
        env["UV_DEFAULT_INDEX"] = index
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        if proxy:
            env["HTTPS_PROXY"] = proxy
            env["HTTP_PROXY"] = proxy
            env.setdefault("NO_PROXY", "pypi.tuna.tsinghua.edu.cn,127.0.0.1,localhost")
            log(f"using proxy: {proxy}")
        else:
            log("no proxy detected (set HTTPS_PROXY if GitHub / PyTorch is slow)")
        log(f"index: {index}")
        log("running `uv sync` (first run downloads several GB; later runs reuse the uv cache) ...")
        proc = subprocess.run([uv, "sync"], cwd=str(runtime), env=env)
        if proc.returncode != 0:
            fail(f"`uv sync` failed with code {proc.returncode}", code=1)

        venv = runtime / ".venv"
        if venv.is_dir():
            log(f"venv: {venv} ({_dir_size_mb(venv):.0f} MB)")
            sync_ok = True
        else:
            log("WARNING: .venv not found after `uv sync`")

    # 9) pre-fetch ASR models: reuse any local cache, download only what is missing
    if args.models:
        for model in [m.strip() for m in args.models.split(",") if m.strip()]:
            prefetch_model(runtime, model, proxy)

    # 10) optional local LLM backend
    if args.with_ollama:
        provision_ollama(proxy, auto=auto)
        ensure_ollama_ready(args.ollama_model, auto=auto)

    # 11) the new runtime is ready: remove migrated pre-1.3 state
    _cleanup_legacy(legacy_home, legacy_tools, runtime, sync_ok)

    if not _is_program(runtime):
        fail("bootstrap finished but cli.py is still missing", code=1)
    log(f"runtime ready: {runtime}")
    return 0


SELFTEST_SENTENCE = "今天天气不错，我们一起去公园散步吧。"
# Two entries on purpose: upstream pyVideoTrans emits no file for a single-line
# SRT with Edge-TTS (see references/troubleshooting.md).
SELFTEST_SRT = (
    "1\n00:00:00,000 --> 00:00:02,000\nHello, this is a self test.\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nEverything is working as expected.\n"
)


def _try_resolve_home(args) -> tuple[Path | None, str]:
    explicit = getattr(args, "home", None)
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return (path, "flag") if _is_program(path) else (None, "flag")
    env = os.environ.get("PYVIDEOTRANS_HOME")
    if env:
        path = Path(env).expanduser().resolve()
        return (path, "env") if _is_program(path) else (None, "env")
    if _is_program(BUNDLED_HOME):
        return BUNDLED_HOME, "bundled"
    external = Path(DEFAULT_HOME).expanduser().resolve()
    return (external, "external") if _is_program(external) else (None, "none")


def _selftest_finish(results: list[dict], started: float, args, work: Path) -> int:
    failed = sum(1 for r in results if r["ok"] is False)
    skipped = sum(1 for r in results if r["ok"] is None)
    passed = sum(1 for r in results if r["ok"] is True)
    summary = {
        "ok": failed == 0,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "elapsed_seconds": round(time.time() - started, 1),
        "work_dir": str(work),
        "stages": results,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        log("")
        log(f"RESULT: {passed} passed, {failed} failed, {skipped} skipped "
            f"in {summary['elapsed_seconds']}s")
        log(f"work dir: {work}" + ("" if args.keep else " (removed)"))
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    return 0 if failed == 0 else 1


def cmd_selftest(args) -> int:
    """Generate test media and exercise ffmpeg, ASR, TTS and (optionally) translation."""
    started = time.time()
    results: list[dict] = []
    marks = [started]

    def record(name: str, ok, detail: str = "") -> None:
        now = time.time()
        elapsed = round(now - marks[0], 1)
        marks[0] = now
        results.append({"stage": name, "ok": ok, "detail": detail, "seconds": elapsed})
        if not args.json:
            status = "PASS" if ok is True else ("SKIP" if ok is None else "FAIL")
            log(f"[{status}] {name}" + (f" - {detail}" if detail else "") + f"  [{elapsed}s]")

    work = Path(tempfile.mkdtemp(prefix="trans-selftest-"))

    home, source = _try_resolve_home(args)
    if home is None:
        record("runtime", False, "no pyVideoTrans runtime; run `setup`")
        return _selftest_finish(results, started, args, work)
    record("runtime", True, f"{home} ({source})")

    uv = os.environ.get("UV_BIN") or shutil.which("uv")
    if not uv:
        bundled = TOOLS_DIR / ("uv.exe" if IS_WINDOWS else "uv")
        uv = str(bundled) if bundled.is_file() else None
    if not uv:
        record("uv", False, "not found; run `setup`")
        return _selftest_finish(results, started, args, work)
    record("uv", True, uv)

    def run(cmd, timeout=900):
        return subprocess.run(
            cmd, cwd=str(home), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )

    probe = run([uv, "run", "python", "-c",
                 "import torch;print(torch.__version__);print(torch.cuda.is_available())"], timeout=300)
    if probe.returncode != 0:
        record("python/torch", False, (probe.stderr or probe.stdout or "").strip()[-200:])
        return _selftest_finish(results, started, args, work)
    lines = probe.stdout.strip().splitlines()
    record("python/torch", True, f"torch {lines[0]}, cuda={lines[1] if len(lines) > 1 else '?'}")

    ff = resolve_ffmpeg(auto=False, proxy=None)
    if not ff:
        record("ffmpeg", False, "not found; run `setup`")
        return _selftest_finish(results, started, args, work)
    record("ffmpeg", True, ff[0])

    video = work / "selftest.mp4"
    enc = subprocess.run(
        [ff[0], "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=0x203040:s=320x180:d=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    record("ffmpeg encode", enc.returncode == 0 and video.is_file(),
           video.name if video.is_file() else (enc.stderr or "")[-200:])

    speech = work / "speech.mp3"
    edge = run([uv, "run", "edge-tts", "--voice", "zh-CN-XiaoxiaoNeural",
                "--text", SELFTEST_SENTENCE, "--write-media", str(speech)], timeout=180)
    edge_ok = edge.returncode == 0 and speech.is_file() and speech.stat().st_size > 1000
    record("edge-tts (network)", edge_ok,
           "generated speech" if edge_ok else (edge.stderr or "")[-200:])
    if not edge_ok:
        return _selftest_finish(results, started, args, work)

    stt_dir = work / "stt_out"
    stt = run([uv, "run", "cli.py", "--task", "stt", "--name", str(speech),
               "--model_name", "tiny", "--output-dir", str(stt_dir), "--cuda"])
    srt_files = list(stt_dir.glob("*.srt"))
    body = srt_files[0].read_text(encoding="utf-8", errors="replace") if srt_files else ""
    recognised = any("一" <= ch <= "鿿" for ch in body)
    record("speech recognition", stt.returncode == 0 and recognised,
           f"{len(body)} chars, model=tiny" if recognised else (stt.stderr or "")[-200:])

    tts_in = work / "tts_in.srt"
    tts_in.write_text(SELFTEST_SRT, encoding="utf-8")
    tts_dir = work / "tts_out"
    tts = run([uv, "run", "cli.py", "--task", "tts", "--name", str(tts_in),
               "--tts_type", "0", "--voice_role", "en-US-GuyNeural",
               "--target_language_code", "en", "--output-dir", str(tts_dir)])
    audio = [p for p in tts_dir.glob("*") if p.suffix.lower() in (".wav", ".mp3", ".m4a")]
    record("speech synthesis", tts.returncode == 0 and bool(audio),
           audio[0].name if audio else (tts.stderr or "")[-200:])

    params = load_params(home)
    tidx = int(params.get("translate_type", 0) or 0)
    tname = CANONICAL.get(tidx, "")
    cfg = CHANNEL_CONFIG.get(tname, {})
    key = params.get(cfg.get("key", ""), "") if cfg.get("key") else ""
    if tname in ("ollama", "localllm") or key:
        trans_in = work / "trans_in.srt"
        trans_in.write_text("1\n00:00:00,000 --> 00:00:02,000\n今天天气不错。\n", encoding="utf-8")
        trans_dir = work / "trans_out"
        if tname in ("ollama", "localllm"):
            try:
                ensure_ollama_ready(params.get("localllm_model") or "qwen2.5:3b", auto=True)
            except SystemExit:
                record("translation", None, "Ollama unavailable; skipped")
                return _selftest_finish(results, started, args, work)
        trans = run([uv, "run", "cli.py", "--task", "sts", "--name", str(trans_in),
                     "--translate_type", str(tidx), "--source_language_code", "zh-cn",
                     "--target_language_code", "en", "--output-dir", str(trans_dir)])
        outs = list(trans_dir.glob("*.srt"))
        record("translation", trans.returncode == 0 and bool(outs),
               f"channel={tname}" if outs else (trans.stderr or "")[-200:])
    else:
        record("translation", None,
               f"channel={tname or '?'} has no key; set DEEPSEEK_API_KEY or use --channel ollama")

    return _selftest_finish(results, started, args, work)


def cmd_doctor(args) -> int:
    info = detect_environment()
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        log("Detected environment:")
        for key, value in info.items():
            log(f"  {key:22} {value}")
    return 0


def cmd_check(args) -> int:
    home, home_source = resolve_home_info(args)
    params = load_params(home)

    try:
        version = re.search(
            r"VERSION\s*=\s*['\"]([^'\"]+)['\"]",
            (home / "videotrans" / "__init__.py").read_text(encoding="utf-8", errors="replace"),
        )
        version = version.group(1) if version else "unknown"
    except Exception:  # noqa: BLE001
        version = "unknown"

    tidx = int(params.get("translate_type", 0) or 0)
    tname = CANONICAL.get(tidx, f"index-{tidx}")
    cfg = CHANNEL_CONFIG.get(tname, {})

    info = {
        "wrapper_version": VERSION,
        "home": str(home),
        "home_source": home_source,
        "bundled_runtime": str(BUNDLED_HOME),
        "pyvideotrans_version": version,
        "uv": shutil.which("uv") or "(missing)",
        "ffmpeg": shutil.which("ffmpeg") or "(missing)",
        "ffprobe": shutil.which("ffprobe") or "(missing)",
        "default_translate_channel": f"{tname} ({tidx})",
        "configured_api_key": redact(params.get(cfg.get("key", ""))) if cfg.get("key") else "n/a",
        "configured_model": params.get(cfg.get("model", ""), "") if cfg.get("model") else "n/a",
        "default_asr_model": DEFAULT_ASR_MODEL,
        "ollama_api": params.get("localllm_api", ""),
    }

    if args.gpu:
        try:
            proc = subprocess.run(
                [uv_bin(), "run", "--project", str(home), "python", "-c",
                 "import torch;print(torch.__version__);"
                 "print(torch.cuda.is_available());"
                 "print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '-')"],
                cwd=str(home), capture_output=True, text=True, timeout=180,
                encoding="utf-8", errors="replace",
            )
            lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
            info["torch"] = lines[0] if len(lines) > 0 else "?"
            info["cuda_available"] = lines[1] if len(lines) > 1 else "?"
            info["gpu"] = lines[2] if len(lines) > 2 else "?"
        except Exception as exc:  # noqa: BLE001
            info["gpu_error"] = str(exc)

    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        log(f"pyVideoTrans {info['pyvideotrans_version']}  ({home})")
        for key, value in info.items():
            if key in ("wrapper_version", "home", "pyvideotrans_version"):
                continue
            log(f"  {key:28} {value}")
        missing = [k for k in ("uv", "ffmpeg", "ffprobe") if info[k] == "(missing)"]
        if missing:
            log(f"\nWARNING: missing required tools: {', '.join(missing)}")
    return 0


def cmd_channels(args) -> int:
    if args.json:
        print(json.dumps({"translate": TRANSLATE_CHANNELS, "tts": TTS_CHANNELS}, indent=2))
        return 0
    log("Translation channels (--channel NAME or index):")
    for name, idx in sorted(TRANSLATE_CHANNELS.items(), key=lambda kv: (kv[1], kv[0])):
        log(f"  {idx:>2}  {name}")
    log("\nTTS channels (--tts-channel NAME or index):")
    for name, idx in sorted(TTS_CHANNELS.items(), key=lambda kv: (kv[1], kv[0])):
        log(f"  {idx:>2}  {name}")
    return 0


def cmd_voices(args) -> int:
    home = resolve_home(args)
    path = home / "videotrans" / "voicejson" / "edge_tts.json"
    if not path.is_file():
        fail(f"voice list not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    lang = args.language.lower()
    locale = (args.locale or "").strip()

    voices: dict[str, str] = {}
    if lang in data and data[lang]:
        voices = data[lang]
    elif lang in ("zh-cn", "zh-tw", "zh") and data.get("zh"):
        voices = data["zh"]
    else:
        voices = data.get(lang, {})

    items = sorted(voices.items(), key=lambda kv: kv[1])
    if locale:
        items = [kv for kv in items if kv[1].lower().startswith(locale.lower() + "-")]
    if lang == "zh-cn" and not locale:
        items = [kv for kv in items if kv[1].startswith("zh-CN-")]
    if lang == "zh-tw" and not locale:
        items = [kv for kv in items if kv[1].startswith("zh-TW-")]

    if not items:
        fail(f"no Edge-TTS voices found for language '{args.language}'"
             + (f" / locale '{locale}'" if locale else ""))
    if args.json:
        print(json.dumps([{"id": vid, "name": name} for name, vid in items],
                         ensure_ascii=False, indent=2))
    else:
        for name, vid in items:
            log(f"{vid:45} {name}")
    return 0


def cmd_test_channel(args) -> int:
    home = resolve_home(args)
    params = load_params(home)
    _, name = resolve_translate(args.channel)
    cfg = CHANNEL_CONFIG.get(name)
    if not cfg:
        fail(f"test-channel is not supported for '{name}' (no OpenAI-compatible endpoint). "
             f"Run a real task instead, e.g. `sts sample.srt --channel {name}`.")

    base = args.api_base or (params.get(cfg.get("api", ""), "") if cfg.get("api") else "")
    base = base or TEST_BASE_URLS.get(name, "")
    if not base:
        fail(f"no known base URL for '{name}'. Pass --api-base.")

    key = args.api_key
    if not key and cfg.get("key"):
        key = params.get(cfg["key"], "")
    if not key and cfg.get("env"):
        key = os.environ.get(cfg["env"] or "", "")
    if not key and name in ("ollama", "localllm", "openai_compatible", "relay"):
        key = "ollama"

    model = args.model or (params.get(cfg["model"], "") if cfg.get("model") else "")
    if not model:
        model = {"deepseek": "deepseek-flash", "ollama": "qwen2.5:3b"}.get(name, "")
    if not model:
        fail(f"no model configured for '{name}'. Pass --model.")

    url = base.rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
        "max_tokens": 8,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        print(f"OK  {name}  model={model}  base={base}")
        print(body[:300])
        return 0
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        fail(f"{name} rejected the request (HTTP {exc.code}): {detail}", code=1)
    except Exception as exc:  # noqa: BLE001
        fail(f"{name} request failed: {exc}", code=1)


def cmd_run(args) -> int:
    home = resolve_home(args)
    params = load_params(home)

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        fail(f"input file not found: {input_path}")

    task = args.command

    # ---- resolve translation channel (CLI defaults to 0 = Google, so we
    #      always pass it explicitly, defaulting to params.json).
    translate_idx = -1
    translate_name = ""
    if task in ("vtv", "sts"):
        if args.channel:
            translate_idx, translate_name = resolve_translate(args.channel)
        else:
            translate_idx = int(params.get("translate_type", 0) or 0)
            translate_name = CANONICAL.get(translate_idx, "")
        changes = configure_channel(home, params, translate_name, args)
        if changes:
            log(f"channel config updated: {', '.join(sorted(changes))}")

    # ---- resolve TTS channel
    tts_idx = -1
    if task in ("vtv", "tts"):
        if args.tts_channel:
            tts_idx, _ = resolve_tts(args.tts_channel)
        else:
            tts_idx = int(params.get("tts_type", 0) or 0)

    # ---- output dir
    outdir = resolve_outdir(home, input_path, args.outdir, args.overwrite)

    # ---- build upstream command
    # --no-sync: the venv is already provisioned by setup; without it uv
    # re-resolves the project on every task and can fail on a flaky GitHub
    # fetch (e.g. chatterbox-tts), aborting an otherwise healthy dub.
    cmd = [uv_bin(), "run", "--no-sync", "cli.py", "--task", task,
           "--name", str(input_path), "--output-dir", str(outdir)]

    if task in ("vtv", "stt"):
        cmd += ["--model_name", args.asr_model]
        cmd += ["--recogn_type", str(args.recogn_type)]
        if args.cuda:
            cmd += ["--cuda"]

    if task in ("vtv", "sts"):
        cmd += ["--translate_type", str(translate_idx)]

    if task == "vtv":
        cmd += ["--source_language_code", args.source]
        cmd += ["--target_language_code", args.target]
        cmd += ["--tts_type", str(tts_idx)]
        cmd += ["--subtitle_type", str(args.subtitle_type)]
        voice = args.voice or DEFAULT_VOICES.get(args.target.lower())
        if voice:
            cmd += ["--voice_role", voice]

    elif task == "stt":
        if args.source:
            cmd += ["--source_language_code", args.source]

    elif task == "sts":
        cmd += ["--target_language_code", args.target]
        if args.source:
            cmd += ["--source_language_code", args.source]

    elif task == "tts":
        cmd += ["--tts_type", str(tts_idx)]
        voice = args.voice or DEFAULT_VOICES.get((args.target or "").lower())
        if voice:
            cmd += ["--voice_role", voice]
        if args.target:
            cmd += ["--target_language_code", args.target]

    if args.no_clear_cache:
        cmd += ["--no-clear-cache"]
    if args.extra_args:
        cmd += shlex.split(args.extra_args)

    # Upstream limitation: one subtitle line + Edge-TTS yields no output file.
    if task == "tts" and tts_idx == 0 and input_path.suffix.lower() in (".srt", ".txt"):
        try:
            entries = sum(1 for line in input_path.read_text(encoding="utf-8", errors="replace").splitlines()
                          if "-->" in line)
        except Exception:  # noqa: BLE001
            entries = 0
        if entries == 1:
            log("WARNING: pyVideoTrans produces no output for a single-line SRT with Edge-TTS "
                "(upstream limitation). Add a second line or use a different TTS channel.")

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    if os.name == "nt":
        # pyVideoTrans is a separate Python process; make it load the hidden
        # subprocess shim too, so its `indextts2.cmd stop` cannot flash a cmd
        # window (the parent's Popen patch does not cross process boundaries).
        site = SCRIPT_DIR / "_winhide_site"
        if site.is_dir():
            prev = env.get("PYTHONPATH", "")
            if str(site) not in prev.split(os.pathsep):
                env["PYTHONPATH"] = str(site) + (os.pathsep + prev if prev else "")

    # Expose provisioned tools to the child (cache-first; nothing is downloaded here).
    tool_paths = []
    ff = resolve_ffmpeg(auto=False, proxy=None)
    if ff:
        tool_paths.append(str(Path(ff[0]).parent))
    ollama_bin = resolve_ollama_binary()
    if ollama_bin:
        tool_paths.append(str(Path(ollama_bin).parent))
    if tool_paths:
        env["PATH"] = os.pathsep.join(tool_paths + [env.get("PATH", "")])

    if args.dry_run:
        log("DRY RUN - command that would run:")
        log("  " + " ".join(shlex.quote(c) for c in cmd))
        log(f"  cwd: {home}")
        log(f"  output: {outdir}")
        return 0

    # Local LLM channel: reuse a running Ollama, or start/pull what is missing.
    if translate_name in ("ollama", "localllm", "openai_compatible", "relay"):
        api = params.get("localllm_api", "")
        is_local = (not api) or ("127.0.0.1" in api) or ("localhost" in api)
        if is_local and not args.no_auto_ollama:
            ensure_ollama_ready(params.get("localllm_model") or "qwen2.5:3b")

    log(f"task      : {task}")
    log(f"input     : {input_path}")
    log(f"output dir: {outdir}")
    if translate_name:
        log(f"translate : {translate_name} ({translate_idx})")
    log("")

    before = snapshot(outdir)
    started = time.time()
    proc = subprocess.run(cmd, cwd=str(home), env=env)
    elapsed = time.time() - started

    after = snapshot(outdir)
    produced = changed_files(before, after)
    # Guard against filesystem timestamp granularity.
    produced = [p for p in produced if p.stat().st_mtime >= started - 2] or produced

    summary = {
        "task": task,
        "input": str(input_path),
        "output_dir": str(outdir),
        "translate_channel": translate_name or None,
        "exit_code": proc.returncode,
        "elapsed_seconds": round(elapsed, 1),
        "files": [{"path": str(p), "bytes": p.stat().st_size} for p in produced],
    }

    if proc.returncode != 0:
        if args.json:
            print(json.dumps({**summary, "ok": False}, ensure_ascii=False, indent=2))
        else:
            log(f"\n[FAILED] pyVideoTrans exited with code {proc.returncode} after {elapsed:.1f}s")
            log_path = latest_log(home)
            if log_path:
                log(f"last log: {log_path}")
        return 1

    if not produced:
        if args.json:
            print(json.dumps({**summary, "ok": False, "error": "no output files"},
                             ensure_ascii=False, indent=2))
        else:
            log(f"\n[WARNING] task reported success but no files appeared in {outdir}")
        return 2

    if args.json:
        print(json.dumps({**summary, "ok": True}, ensure_ascii=False, indent=2))
    else:
        log(f"\n[DONE] {task} finished in {elapsed:.1f}s")
        log(f"Output: {outdir}")
        for item in summary["files"]:
            size = item["bytes"]
            human = f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.1f} KB"
            log(f"  - {Path(item['path']).name:40} {human}")
    return 0


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------
def add_common(parser):
    parser.add_argument("--home", help="pyVideoTrans checkout (or set PYVIDEOTRANS_HOME)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")


def add_run_common(parser):
    add_common(parser)
    parser.add_argument("input", help="input file (video/audio/SRT depending on task)")
    parser.add_argument("--outdir", help="output directory (default: <input>_translated next to the input)")
    parser.add_argument("--overwrite", action="store_true",
                        help="allow using a non-empty --outdir (its contents WILL be deleted by upstream)")
    parser.add_argument("--asr-model", default=DEFAULT_ASR_MODEL,
                        help=f"Whisper model: tiny/base/small/medium/large-v2/large-v3/... (default: {DEFAULT_ASR_MODEL})")
    parser.add_argument("--recogn-type", type=int, default=0,
                        help="ASR provider index (0 = faster-whisper)")
    parser.add_argument("--cuda", dest="cuda", action="store_true", default=True,
                        help="use CUDA when available (default)")
    parser.add_argument("--no-cuda", dest="cuda", action="store_false", help="force CPU")
    parser.add_argument("--channel", help="translation channel name or index (default: params.json translate_type)")
    parser.add_argument("--api-key", help="API key for the selected channel (also read from env)")
    parser.add_argument("--api-base", help="custom base URL for OpenAI-compatible channels")
    parser.add_argument("--model", help="LLM model name for the selected channel")
    parser.add_argument("--tts-channel", help="TTS channel name or index (default: params.json tts_type)")
    parser.add_argument("--voice", help="TTS voice id (default: per-language Edge voice)")
    parser.add_argument("--target", help="target language code, e.g. en / zh-cn / ja")
    parser.add_argument("--source", help="source language code, e.g. zh-cn (auto-detected for stt/sts if omitted)")
    parser.add_argument("--subtitle-type", type=int, default=1, choices=range(0, 5),
                        help="0=none 1=hard 2=soft 3=dual-hard 4=dual-soft (default 1)")
    parser.add_argument("--no-clear-cache", action="store_true",
                        help="do not let upstream wipe --output-dir at task start")
    parser.add_argument("--no-auto-ollama", action="store_true",
                        help="do not auto-start Ollama / pull the model for local channels")
    parser.add_argument("--extra-args", help="extra args passed to cli.py, e.g. \"--fix_punc --rephrase\"")
    parser.add_argument("--dry-run", action="store_true", help="print the command without running it")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trans_cli.py",
        description="Safe, self-contained CLI wrapper around pyVideoTrans.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run `python trans_cli.py channels` to list channel names.",
    )
    parser.add_argument("--version", action="version", version=f"trans_cli {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="create/update the vendored runtime and install dependencies")
    add_common(p)
    p.add_argument("--copy-from", help="source pyVideoTrans checkout to copy into vendor/")
    p.add_argument("--force", action="store_true",
                   help="re-run provisioning; re-copy only when --copy-from is given")
    p.add_argument("--migrate-from", metavar="DIR",
                   help="migrate models/tools/params from a pre-1.3 package root "
                        "or a pyvideotrans tree")
    p.add_argument("--no-sync", action="store_true",
                   help="probe/migrate/copy only, do not run `uv sync`")
    p.add_argument("--index-url", help=f"PyPI index (default: {DEFAULT_INDEX})")
    p.add_argument("--proxy", help="HTTP(S) proxy, e.g. http://127.0.0.1:2080")
    p.add_argument("--torch", choices=("auto", "cpu", "cu128"), default="auto",
                   help="PyTorch build: auto detects an NVIDIA GPU (default)")
    p.add_argument("--models", default="",
                   help="comma-separated ASR models to pre-fetch, e.g. large-v3-turbo,tiny")
    p.add_argument("--with-ollama", action="store_true",
                   help="download a portable Ollama and pull the model (large download)")
    p.add_argument("--ollama-model", default="qwen2.5:3b", help="model to pull with --with-ollama")
    p.add_argument("--no-download", action="store_true",
                   help="never download; fail or skip when something is missing")
    p.add_argument("--keep-pynini", action="store_true",
                   help="keep pynini/WeTextProcessing even if the platform has no wheels")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("selftest", help="end-to-end self test (ffmpeg, ASR, TTS, optional translation)")
    add_common(p)
    p.add_argument("--keep", action="store_true", help="keep the temporary work directory")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("doctor", help="detect the machine, tools and caches")
    add_common(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("check", help="verify installation and show configuration")
    add_common(p)
    p.add_argument("--gpu", action="store_true", help="also run a torch/CUDA probe (slow)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("channels", help="list translation and TTS channels")
    add_common(p)
    p.set_defaults(func=cmd_channels)

    p = sub.add_parser("voices", help="list Edge-TTS voices")
    add_common(p)
    p.add_argument("--language", required=True, help="language code, e.g. en, zh-cn, ja")
    p.add_argument("--locale", help="filter by locale, e.g. en-US")
    p.set_defaults(func=cmd_voices)

    p = sub.add_parser("test-channel", help="verify an AI channel's credentials with one tiny request")
    add_common(p)
    p.add_argument("--channel", required=True, help="channel name, e.g. deepseek / ollama")
    p.add_argument("--api-key")
    p.add_argument("--api-base")
    p.add_argument("--model")
    p.add_argument("--timeout", type=int, default=60)
    p.set_defaults(func=cmd_test_channel)

    for task, help_text in (
        ("vtv", "translate a video: ASR -> translate -> TTS -> mux"),
        ("stt", "transcribe audio/video to SRT"),
        ("sts", "translate an SRT subtitle file"),
        ("tts", "synthesize speech from an SRT"),
    ):
        p = sub.add_parser(task, help=help_text)
        add_run_common(p)
        p.set_defaults(func=cmd_run)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Per-task required arguments (kept out of argparse so `--help` stays readable).
    if args.command == "vtv":
        if not args.source:
            parser.error("vtv requires --source (e.g. --source zh-cn)")
        if not args.target:
            parser.error("vtv requires --target (e.g. --target en)")
    elif args.command == "sts":
        if not args.target:
            parser.error("sts requires --target (e.g. --target en)")
    elif args.command == "tts":
        if not args.target and not args.voice:
            parser.error("tts requires --target or --voice")

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
