#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ask an LLM (local first, DeepSeek fallback) to audit subtitle boundaries.

The rule pass in ``resegment_srt.py`` merges obvious fragments, but it cannot
tell whether two well-formed-looking cues belong to one sentence.  This pass
sends the whole cue list to the LLM and asks it to *only* merge adjacent cues
(or keep them), never to rewrite, translate, add, or remove words.

Every proposed group is validated locally before use:
  * groups must cover every input cue exactly once, in order;
  * the new text must be >=85% similar to the concatenated originals;
  * numbers and ASCII terms from the original must still be present;
  * length may not change by more than 25%.

On API/JSON/validation failure the input SRT is copied to the output unchanged,
so this step can never make the pipeline worse.  Results are cached by content.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _winhide import utf8_stdio  # noqa: E402
from resegment_srt import (  # noqa: E402
    parse_srt,
    split_long,
    split_sentences,
    write_srt,
)

utf8_stdio()

SKILL = Path(__file__).resolve().parent.parent
CACHE = SKILL / ".runtime" / "resegment_cache"


def params_path() -> Path | None:
    """Locate pyVideoTrans params.json.

    The runtime lives outside the skill (see vendor_paths.py); pipeline.py
    passes PYVIDEOTRANS_HOME, and a manual run falls back to config.json.
    """
    home = os.environ.get("PYVIDEOTRANS_HOME", "").strip()
    if not home:
        try:
            cfg = json.loads((SKILL / "config.json").read_text(encoding="utf-8"))
            home = str(cfg.get("pyvideotrans_home") or "")
        except Exception:
            home = ""
    return (Path(home).expanduser() / "videotrans" / "params.json") if home else None
MODEL_DEFAULT = "deepseek-flash"
BASE_DEFAULT = "https://api.deepseek.com/v1"
TERM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+.\-/]*")


def _is_local(base: str) -> bool:
    return base.startswith("http://") or "127.0.0.1" in base or "localhost" in base


def load_credentials() -> tuple[str, str, str]:
    """Resolve the review endpoint.

    pipeline.py passes VTV_RESEGMENT_API / _MODEL / _KEY for each provider it
    tries (local first, DeepSeek second).  Standalone runs fall back to the
    pyVideoTrans params: a localhost ``localllm_api`` wins over DeepSeek.
    """
    key = os.environ.get("VTV_RESEGMENT_KEY", "") or os.environ.get("DEEPSEEK_API_KEY", "")
    model = os.environ.get("VTV_RESEGMENT_MODEL", "")
    base = os.environ.get("VTV_RESEGMENT_API", "") or os.environ.get("DEEPSEEK_BASE_URL", "")
    path = params_path()
    params = {}
    if path is not None:
        try:
            params = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            params = {}
    model = model or params.get("deepseek_model", "")
    if not base:
        local_api = str(params.get("localllm_api") or "")
        if local_api and _is_local(local_api):
            base = local_api
            model = os.environ.get("VTV_RESEGMENT_MODEL", "") or \
                str(params.get("localllm_model") or "") or model
    if not key and not base:
        key = str(params.get("deepseek_key") or "")
    base = (base or BASE_DEFAULT).rstrip("/")
    if _is_local(base) and not os.environ.get("VTV_RESEGMENT_KEY"):
        key = "local"
    return key, model or MODEL_DEFAULT, base


def cache_key(texts: list[str], max_chars: int, model: str) -> str:
    raw = f"v2|{model}|{max_chars}|" + "\n".join(texts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ends_sentence(text: str) -> bool:
    return text.rstrip().endswith(("。", "！", "？", ".", "!", "?", "…", "；", ";"))


def split_chunks(blocks: list[dict], size: int) -> list[list[dict]]:
    """Split cues into chunks, preferring a sentence boundary near the limit.

    Long transcripts make the model echo every cue back, so a single response
    can exceed the provider's output cap and arrive truncated.  Auditing each
    chunk separately keeps responses small enough to arrive complete.
    """
    if size <= 0 or len(blocks) <= size:
        return [blocks]
    out: list[list[dict]] = []
    start = 0
    n = len(blocks)
    while start < n:
        end = min(start + size, n)
        if end < n:
            floor = max(start + size // 2, start + 1)
            for k in range(end, floor, -1):
                if _ends_sentence(blocks[k - 1]["text"]):
                    end = k
                    break
        out.append(blocks[start:end])
        start = end
    return out


def build_prompt(texts: list[str], max_chars: int) -> str:
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    return (
        "你是字幕断句审校器。下面是按顺序编号的字幕块；ASR/翻译可能把一句完整的话"
        "切成了多个块。只检查断句是否完整，不要翻译、改写、增删任何词。\n"
        "规则：\n"
        f"- 允许把相邻的 2-3 个块合并成一个 group，合并后文本不超过 {max_chars} 个字符；\n"
        "- 不允许拆分单个块；只能合并或保持；\n"
        "- 合并后必须不超过长度上限；可以删虚词/重复词、轻微压缩措辞，但不得改"
        "事实、数字、英文术语、人物/产品名，不得漏信息、不得改变原意；\n"
        "- 每组 text 必须是这些块原文按顺序拼接（允许补一个逗号/空格连接）；\n"
        "- 必须保留原文所有数字和英文术语；\n"
        "- groups 必须按顺序覆盖 1..N，每个块恰好出现一次。\n"
        '只输出 JSON，不要 markdown：{"groups":[{"blocks":[1,2],"text":"..."}]}\n'
        f"字幕块：\n{lines}"
    )


def call_llm(texts: list[str], max_chars: int, key: str, model: str,
             base: str) -> list[dict] | None:
    try:
        max_tokens = int(os.environ.get("VTV_RESEGMENT_MAX_TOKENS") or 8192)
    except (TypeError, ValueError):
        max_tokens = 8192
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "只输出严格 JSON，不要解释。"},
            {"role": "user", "content": build_prompt(texts, max_chars)},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    if not _is_local(base):
        # deepseek-flash is a reasoning model; disable thinking so content is JSON.
        # Local servers either ignore or reject this field, so send it only online.
        body["thinking"] = {"type": "disabled"}
    try:
        timeout = float(os.environ.get("VTV_RESEGMENT_TIMEOUT") or 180)
    except (TypeError, ValueError):
        timeout = 180.0
    try:
        data = _post(base, body, key, timeout)
    except HTTPError as exc:
        if exc.code not in (400, 422):
            print(f"[resegment-check] {base} unavailable, fallback to rules: {exc}")
            return None
        body.pop("response_format", None)
        body.pop("thinking", None)
        try:
            data = _post(base, body, key, timeout)
        except Exception as exc2:  # noqa: BLE001
            print(f"[resegment-check] {base} unavailable, fallback to rules: {exc2}")
            return None
    except Exception as exc:  # noqa: BLE001
        print(f"[resegment-check] {base} unavailable, fallback to rules: {exc}")
        return None
    try:
        content = data["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        obj = json.loads(content)
        groups = obj.get("groups")
        return groups if isinstance(groups, list) else None
    except Exception as exc:  # noqa: BLE001
        print(f"[resegment-check] bad response from {base}: {exc}")
        return None


def _transient(exc: BaseException) -> bool:
    """Connection resets happen under load (llama-server WinError 10054);
    one retry usually succeeds and avoids falling back to another provider."""
    if isinstance(exc, HTTPError):
        return exc.code in (502, 503, 504)
    if isinstance(exc, URLError):
        exc = exc.reason if isinstance(exc.reason, BaseException) else exc
    return isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                            BrokenPipeError))


def _post(base: str, body: dict, key: str, timeout: float) -> dict:
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            if attempt == 2 or not _transient(exc):
                raise
            print(f"[resegment-check] {base} transient error, retrying once: {exc}")
            time.sleep(2.0)
    raise AssertionError("unreachable")


def validate_groups(groups, blocks) -> list[dict] | None:
    if not groups:
        return None
    n = len(blocks)
    expected = 1
    out = []
    for g in groups:
        if not isinstance(g, dict):
            return None
        idx = g.get("blocks")
        text = g.get("text")
        if not isinstance(idx, list) or not idx or not isinstance(text, str):
            return None
        if idx != list(range(idx[0], idx[-1] + 1)) or idx[0] != expected:
            return None
        if len(idx) > 3:
            return None
        expected = idx[-1] + 1
        out.append({"blocks": idx, "text": re.sub(r"\s+", " ", text).strip()})
    if expected != n + 1 or any(not g["text"] for g in out):
        return None
    return out


def repair_groups(groups, blocks) -> list[dict] | None:
    """Validate coverage, splitting oversized groups into <=3-block units.

    Small models often honour sentence boundaries but ignore the 3-block cap.
    Splitting those groups mechanically keeps the boundary information while
    the concatenated original text keeps every word.
    """
    if not groups:
        return None
    n = len(blocks)
    expected = 1
    out = []
    for g in groups:
        if not isinstance(g, dict):
            return None
        idx = g.get("blocks")
        text = g.get("text")
        if not isinstance(idx, list) or not idx or not isinstance(text, str):
            return None
        if idx != list(range(idx[0], idx[-1] + 1)) or idx[0] != expected:
            return None
        expected = idx[-1] + 1
        if len(idx) <= 3:
            out.append({"blocks": idx, "text": re.sub(r"\s+", " ", text).strip()})
            continue
        for i in range(0, len(idx), 3):
            sub = idx[i:i + 3]
            out.append({"blocks": sub,
                        "text": "".join(blocks[j - 1]["text"] for j in sub).strip()})
    if expected != n + 1 or any(not g["text"] for g in out):
        return None
    return out


def group_is_safe(original: str, proposed: str) -> bool:
    a = re.sub(r"\s+", " ", original).strip()
    b = re.sub(r"\s+", " ", proposed).strip()
    if not a or not b:
        return False
    if abs(len(a) - len(b)) / max(1, len(a)) > 0.25:
        return False
    if difflib.SequenceMatcher(None, a, b).ratio() < 0.85:
        return False
    lower = b.lower()
    for term in TERM.findall(a):
        if len(term) >= 2 and term.lower() not in lower:
            return False
    return True


def _timed_chunks(start: int, end: int, text: str, max_chars: int) -> list[dict]:
    chunks: list[str] = []
    for sentence in split_sentences(text):
        chunks.extend(split_long(sentence, max_chars))
    if not chunks:
        chunks = [text]
    total = sum(max(1, len(c)) for c in chunks)
    cursor = start
    span = max(1, end - start)
    out = []
    for chunk in chunks:
        dur = span * max(1, len(chunk)) / total
        out.append({"start": int(round(cursor)),
                    "end": int(round(cursor + dur)), "text": chunk})
        cursor += dur
    out[-1]["end"] = end
    return out


def apply_tts_groups(groups: list[dict], blocks: list[dict],
                     max_chars: int) -> list[dict]:
    """One TTS unit per LLM group, split only when a group exceeds max_chars."""
    out = []
    for g in groups:
        group_blocks = [blocks[i - 1] for i in g["blocks"]]
        original = " ".join(b["text"] for b in group_blocks).strip()
        text = g["text"] if group_is_safe(original, g["text"]) else original
        start, end = group_blocks[0]["start"], group_blocks[-1]["end"]
        if len(text) <= max_chars:
            out.append({"start": start, "end": end, "text": text})
        else:
            out.extend(_timed_chunks(start, end, text, max_chars))
    return out


def apply_groups(groups: list[dict], blocks: list[dict], max_chars: int) -> list[dict]:
    out: list[dict] = []
    for g in groups:
        idx = g["blocks"]
        group_blocks = [blocks[i - 1] for i in idx]
        original = " ".join(b["text"] for b in group_blocks).strip()
        text = g["text"] if group_is_safe(original, g["text"]) else original
        if len(group_blocks) > 1 and len(text) <= max_chars:
            # The LLM merged a fragmented sentence: keep it as one TTS unit.
            chunks = [text]
        else:
            chunks = []
            for sentence in split_sentences(text):
                chunks.extend(split_long(sentence, max_chars))
            if not chunks:
                chunks = [text]
        start, end = group_blocks[0]["start"], group_blocks[-1]["end"]
        total = sum(max(1, len(c)) for c in chunks)
        cursor = start
        span = max(1, end - start)
        for chunk in chunks:
            dur = span * max(1, len(chunk)) / total
            out.append({"start": int(round(cursor)),
                        "end": int(round(cursor + dur)), "text": chunk})
            cursor += dur
        out[-1]["end"] = end
    return out


def audit_blocks(blocks: list[dict], max_chars: int, key: str, model: str,
                 base: str, no_cache: bool = False
                 ) -> tuple[list[dict] | None, int]:
    """Run one LLM audit over a cue chunk; return (TTS units, exit code)."""
    texts = [b["text"] for b in blocks]
    digest = cache_key(texts, max_chars, model)
    cached = CACHE / f"{digest}.json"
    groups = None
    if cached.is_file() and not no_cache:
        try:
            groups = json.loads(cached.read_text(encoding="utf-8")).get("groups")
        except Exception:
            groups = None
    if groups is None:
        groups = call_llm(texts, max_chars, key, model, base)
        if groups is None:
            return None, 3
        valid = repair_groups(groups, blocks)
        if valid is None:
            idx = [g.get("blocks") for g in groups if isinstance(g, dict)][:6]
            print(f"[resegment-check] invalid groups for {len(blocks)} cues: "
                  f"n={len(groups)} first={idx}")
            return None, 4
        groups = valid
        try:
            CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps({"model": model, "groups": groups},
                                         ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    else:
        groups = repair_groups(groups, blocks)
        if groups is None:
            cached.unlink(missing_ok=True)
            return None, 4
    return apply_tts_groups(groups, blocks, max_chars), 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--max-chars", type=int, default=44)
    ap.add_argument("--lang", default="zh-cn")
    ap.add_argument("--tts-output", default="",
                    help="write merged sentence groups for TTS; keep --output as display SRT")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--require-llm", action="store_true",
                    help="exit 3/4 when the LLM call fails so the caller can try "
                         "another provider; otherwise always keep rule-based output")
    args = ap.parse_args()

    src = Path(args.input)
    blocks = parse_srt(src)
    if not blocks:
        print(f"no subtitle blocks in {src}")
        return 2
    texts = [b["text"] for b in blocks]

    def keep_rules(code: int) -> int:
        write_srt(blocks, Path(args.output))
        if args.tts_output:
            write_srt(blocks, Path(args.tts_output))
        return code if args.require_llm else 0

    key, model, base = load_credentials()
    if not key:
        print("[resegment-check] no LLM endpoint configured; keeping rule-based result")
        return keep_rules(3)

    try:
        chunk_size = int(os.environ.get("VTV_RESEGMENT_CHUNK_CUES") or 90)
    except (TypeError, ValueError):
        chunk_size = 90
    chunks = split_chunks(blocks, chunk_size)
    if args.tts_output and len(chunks) > 1:
        write_srt(blocks, Path(args.output))
        units: list[dict] = []
        for i, chunk in enumerate(chunks, 1):
            got, rc = audit_blocks(chunk, args.max_chars, key, model, base,
                                   args.no_cache)
            if got is None:
                print(f"[resegment-check] chunk {i}/{len(chunks)} "
                      f"failed rc={rc}; keeping rule-based result")
                return keep_rules(rc)
            units.extend(got)
        write_srt(units, Path(args.tts_output))
        print(f"resegment-check: {len(blocks)} cues -> {len(units)} TTS units "
              f"in {len(chunks)} chunks -> {args.tts_output}")
        return 0

    digest = cache_key(texts, args.max_chars, model)
    cached = CACHE / f"{digest}.json"
    groups = None
    if cached.is_file() and not args.no_cache:
        try:
            groups = json.loads(cached.read_text(encoding="utf-8")).get("groups")
        except Exception:
            groups = None
    if groups is None:
        groups = call_llm(texts, args.max_chars, key, model, base)
        if groups is None:
            return keep_rules(3)
        valid = repair_groups(groups, blocks)
        if valid is None:
            print("[resegment-check] invalid group list; keeping rule-based result")
            return keep_rules(4)
        groups = valid
        try:
            CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps({"model": model, "groups": groups},
                                         ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    else:
        groups = repair_groups(groups, blocks)
        if groups is None:
            cached.unlink(missing_ok=True)
            return keep_rules(4)

    if args.tts_output:
        # Display cues stay as-is; the merged groups are only TTS units.
        write_srt(blocks, Path(args.output))
        tts_units = apply_tts_groups(groups, blocks, args.max_chars)
        write_srt(tts_units, Path(args.tts_output))
        print(f"resegment-check: {len(groups)} TTS groups, "
              f"display {len(blocks)} cues -> {args.tts_output}")
    else:
        out = apply_groups(groups, blocks, args.max_chars)
        write_srt(out, Path(args.output))
        print(f"resegment-check: {len(groups)} groups, {len(blocks)} -> {len(out)} cues")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
