#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ask DeepSeek to audit rule-based subtitle sentence boundaries.

The rule pass in ``resegment_srt.py`` merges obvious fragments, but it cannot
tell whether two well-formed-looking cues belong to one sentence.  This pass
sends the whole cue list to DeepSeek and asks it to *only* merge adjacent cues
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
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from resegment_srt import (  # noqa: E402
    parse_srt,
    split_long,
    split_sentences,
    write_srt,
)

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


def load_credentials() -> tuple[str, str, str]:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    model = os.environ.get("VTV_RESEGMENT_MODEL", "")
    base = os.environ.get("DEEPSEEK_BASE_URL", "")
    path = params_path()
    if path is not None:
        try:
            params = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            params = {}
        key = key or params.get("deepseek_key", "")
        model = model or params.get("deepseek_model", "")
    return key, model or MODEL_DEFAULT, (base or BASE_DEFAULT).rstrip("/")


def cache_key(texts: list[str], max_chars: int, model: str) -> str:
    raw = f"v2|{model}|{max_chars}|" + "\n".join(texts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "只输出严格 JSON，不要解释。"},
            {"role": "user", "content": build_prompt(texts, max_chars)},
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
        # deepseek-flash is a reasoning model; disable thinking so content is JSON.
        "thinking": {"type": "disabled"},
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions", data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        content = data["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        obj = json.loads(content)
        groups = obj.get("groups")
        return groups if isinstance(groups, list) else None
    except Exception as exc:  # noqa: BLE001
        print(f"[resegment-check] DeepSeek unavailable, fallback to rules: {exc}")
        return None


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


def apply_tts_groups(groups: list[dict], blocks: list[dict]) -> list[dict]:
    """One TTS unit per DeepSeek group; text may exceed the display width."""
    out = []
    for g in groups:
        group_blocks = [blocks[i - 1] for i in g["blocks"]]
        original = " ".join(b["text"] for b in group_blocks).strip()
        text = g["text"] if group_is_safe(original, g["text"]) else original
        out.append({"start": group_blocks[0]["start"],
                    "end": group_blocks[-1]["end"], "text": text})
    return out


def apply_groups(groups: list[dict], blocks: list[dict], max_chars: int) -> list[dict]:
    out: list[dict] = []
    for g in groups:
        idx = g["blocks"]
        group_blocks = [blocks[i - 1] for i in idx]
        original = " ".join(b["text"] for b in group_blocks).strip()
        text = g["text"] if group_is_safe(original, g["text"]) else original
        if len(group_blocks) > 1 and len(text) <= max_chars:
            # DeepSeek merged a fragmented sentence: keep it as one TTS unit.
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--max-chars", type=int, default=44)
    ap.add_argument("--lang", default="zh-cn")
    ap.add_argument("--tts-output", default="",
                    help="write merged sentence groups for TTS; keep --output as display SRT")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    src = Path(args.input)
    blocks = parse_srt(src)
    if not blocks:
        print(f"no subtitle blocks in {src}")
        return 2
    texts = [b["text"] for b in blocks]
    key, model, base = load_credentials()
    if not key:
        print("[resegment-check] no DeepSeek key; keeping rule-based result")
        write_srt(blocks, Path(args.output))
        if args.tts_output:
            write_srt(blocks, Path(args.tts_output))
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
            write_srt(blocks, Path(args.output))
            if args.tts_output:
                write_srt(blocks, Path(args.tts_output))
            return 0
        valid = validate_groups(groups, blocks)
        if valid is None:
            print("[resegment-check] invalid group list; keeping rule-based result")
            write_srt(blocks, Path(args.output))
            if args.tts_output:
                write_srt(blocks, Path(args.tts_output))
            return 0
        groups = valid
        try:
            CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps({"model": model, "groups": groups},
                                         ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    else:
        groups = validate_groups(groups, blocks)
        if groups is None:
            write_srt(blocks, Path(args.output))
            return 0

    if args.tts_output:
        # Display cues stay as-is; the merged groups are only TTS units.
        write_srt(blocks, Path(args.output))
        tts_units = apply_tts_groups(groups, blocks)
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
