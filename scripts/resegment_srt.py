#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Merge fragmented ASR/VTT cues into sentences, then re-split at punctuation.

ASR and VTT cues are timed chunks, not sentences: a complete sentence is often
split across two cues.  Translating those chunks one by one produces the same
split in the target language.  This script repairs the boundary *before*
translation (source language) and *after* translation (target language):

  * adjacent cues are merged when the first one does not end with a sentence
    terminator and the gap is small;
  * English cues that start with a continuation word are also merged;
  * the merged text is split at sentence terminators, and over-long sentences
    are split at commas/spaces near the character limit;
  * time is redistributed proportionally to the character count.

It is deliberately rule-based (no extra API cost).  DeepSeek then translates
complete sentences instead of fragments.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

SENT_END = set("。！？.!?…")
SOFT_BREAK = set("，,、；;：:")
CONTINUATION = re.compile(
    r"^(and|but|so|because|if|when|while|although|though|which|that|who|"
    r"where|with|without|to|of|for|in|on|at|by|from|as|or|nor|yet|still|"
    r"then|thus|however|moreover|also|this|these|those|it|they)\b",
    re.I,
)


def parse_srt(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    blocks = []
    for raw in re.split(r"\n\s*\n", text.strip()):
        lines = [x.strip() for x in raw.splitlines() if x.strip()]
        if len(lines) < 3:
            continue
        m = re.match(
            r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)",
            lines[1])
        if not m:
            continue
        g = list(map(int, m.groups()))
        start = g[0] * 3600000 + g[1] * 60000 + g[2] * 1000 + g[3]
        end = g[4] * 3600000 + g[5] * 60000 + g[6] * 1000 + g[7]
        blocks.append({"start": start, "end": end,
                       "text": " ".join(lines[2:]).strip()})
    return blocks


def sanitize_blocks(blocks: list[dict]) -> list[dict]:
    """Sort cues and remove out-of-order/over-long overlaps.

    VTT exports can contain overlapping speaker cues and occasional cues whose
    end is minutes after the next cue starts.  The aligner later assumes
    monotonic, non-overlapping slots; clamp each cue's end to the next cue's
    start so one bad cue cannot create a multi-minute slot.
    """
    blocks.sort(key=lambda b: (b["start"], b["end"]))
    for i, b in enumerate(blocks):
        b["start"] = max(0, int(b["start"]))
        b["end"] = max(b["start"] + 1, int(b["end"]))
        if i + 1 < len(blocks):
            next_start = max(0, int(blocks[i + 1]["start"]))
            if b["end"] > next_start:
                b["end"] = max(b["start"] + 1, next_start)
    return blocks


def fmt_time(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ends_sentence(text: str) -> bool:
    return bool(text) and text.rstrip()[-1] in SENT_END


def join_text(a: str, b: str) -> str:
    sep = " " if re.search(r"[A-Za-z0-9]$", a) and re.match(r"[A-Za-z0-9]", b) else ""
    return a.rstrip() + sep + b.lstrip()


def split_sentences(text: str) -> list[str]:
    """Split after sentence terminators, keeping the punctuation."""
    parts = re.split(r"(?<=[。！？.!?…])\s*", text.strip())
    return [p.strip() for p in parts if p.strip()]


def split_long(text: str, limit: int) -> list[str]:
    """Split an over-long sentence at a comma/space near the limit."""
    if len(text) <= limit:
        return [text]
    out = []
    rest = text.strip()
    while len(rest) > limit:
        window = rest[:limit + 1]
        cut = -1
        for i in range(len(window) - 1, max(0, limit // 2), -1):
            if window[i - 1] in SOFT_BREAK or window[i - 1].isspace():
                cut = i
                break
        if cut <= 0:
            cut = limit
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        out.append(rest)
    return out


def resegment(blocks: list[dict], max_chars: int, max_gap: int,
              source_lang: str) -> list[dict]:
    groups: list[dict] = []
    for b in blocks:
        if groups:
            a = groups[-1]
            gap = b["start"] - a["end"]
            merge = gap <= max_gap and not ends_sentence(a["text"])
            if not merge and source_lang.lower().startswith("en"):
                merge = (gap <= max_gap
                         and bool(CONTINUATION.match(b["text"].strip())))
            if merge and len(a["text"]) + len(b["text"]) <= max_chars * 1.6:
                a["text"] = join_text(a["text"], b["text"])
                a["end"] = b["end"]
                continue
        groups.append(dict(b))

    out: list[dict] = []
    for g in groups:
        chunks: list[str] = []
        for sentence in split_sentences(g["text"]):
            chunks.extend(split_long(sentence, max_chars))
        if not chunks:
            chunks = [g["text"]]
        total = sum(max(1, len(c)) for c in chunks)
        cursor = g["start"]
        span = max(1, g["end"] - g["start"])
        for chunk in chunks:
            dur = span * max(1, len(chunk)) / total
            start = int(round(cursor))
            end = int(round(cursor + dur))
            out.append({"start": start, "end": end, "text": chunk})
            cursor += dur
        out[-1]["end"] = g["end"]
    return out


def write_srt(blocks: list[dict], path: Path) -> None:
    lines = []
    for i, b in enumerate(blocks, 1):
        lines.append(f"{i}\n{fmt_time(b['start'])} --> {fmt_time(b['end'])}\n"
                     f"{b['text']}\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--max-chars", type=int, default=44,
                    help="max characters per cue (44 = 22 x 2 lines)")
    ap.add_argument("--max-gap", type=int, default=900,
                    help="merge cues separated by at most this many ms")
    ap.add_argument("--source-lang", default="en")
    args = ap.parse_args()

    src = Path(args.input)
    blocks = parse_srt(src)
    if not blocks:
        print(f"no subtitle blocks in {src}")
        return 2
    blocks = sanitize_blocks(blocks)
    merged = resegment(blocks, max(8, args.max_chars), max(0, args.max_gap),
                       args.source_lang)
    write_srt(merged, Path(args.output))
    print(f"resegment: {len(blocks)} -> {len(merged)} blocks "
          f"({src} -> {args.output})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
