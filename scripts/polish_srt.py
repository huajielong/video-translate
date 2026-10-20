# -*- coding: utf-8 -*-
"""Lightweight LLM review of a translated SRT.

Only the target text may change: block count, order and timestamps stay
identical. Batches that fail validation are left untouched.

  python polish_srt.py --srt zh-cn.srt --glossary glossary.txt --out zh-cn.polished.srt
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path

BATCH = 20


def parse_srt(text: str) -> list[dict]:
    blocks = re.split(r"\n\s*\n", text.strip())
    out = []
    for b in blocks:
        lines = [x.rstrip() for x in b.splitlines()]
        if len(lines) < 3:
            continue
        m = re.match(r"\s*(\S+)\s*-->\s*(\S+)\s*$", lines[1])
        if not m:
            continue
        out.append({"time": lines[1].strip(), "text": "\n".join(lines[2:]).strip()})
    return out


def to_srt(items: list[dict]) -> str:
    return "\n\n".join(f"{i}\n{it['time']}\n{it['text']}" for i, it in enumerate(items, 1)) + "\n"


def load_key(model: str, params_path: str) -> tuple[str, str]:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key and params_path and Path(params_path).exists():
        data = json.loads(Path(params_path).read_text(encoding="utf-8"))
        key = data.get("deepseek_key", "")
        model = model or data.get("deepseek_model", "")
    return key, model or "deepseek-flash"


def load_glossary(path: str) -> str:
    if not path or not Path(path).exists():
        return ""
    return "\n".join(l.strip() for l in Path(path).read_text(encoding="utf-8-sig").splitlines()
                     if l.strip() and "=" in l)


def review(batch: list[dict], key: str, model: str, glossary: str) -> list[dict]:
    src = to_srt(batch)
    prompt = f"""你是字幕翻译审校。请只润色下面 SRT 片段中的目标语言文本：
1. 保持每个块的序号和时间轴完全不变；
2. 不合并、不拆分、不删除、不新增任何块；
3. 保持口语化、简洁，适合配音语速；
4. 必须遵守术语表（若出现对应术语）：
{glossary}

只输出 SRT 本身，不要解释、不要代码块。

<SRT>
{src}
</SRT>"""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 8192,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions", data=payload, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data["choices"][0]["message"]["content"]
    text = re.sub(r"^```(?:srt)?\s*|```$", "", text.strip(), flags=re.I | re.M)
    got = parse_srt(text)
    if len(got) != len(batch):
        raise ValueError(f"block count {len(got)} != {len(batch)}")
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--srt", required=True)
    ap.add_argument("--glossary", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="")
    ap.add_argument("--params", default="", help="pyVideoTrans params.json for the key")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    items = parse_srt(Path(args.srt).read_text(encoding="utf-8-sig"))
    key, model = load_key(args.model, args.params)
    if not key:
        raise SystemExit("no DeepSeek key: set DEEPSEEK_API_KEY or pass --params")
    glossary = load_glossary(args.glossary)
    changed = 0
    for start in range(0, len(items), BATCH):
        batch = items[start:start + BATCH]
        try:
            got = review(batch, key, model, glossary)
        except Exception as exc:  # noqa: BLE001
            print(f"batch {start//BATCH+1}: keep original ({exc})")
            continue
        for old, new in zip(batch, got):
            if new["text"].strip() != old["text"].strip():
                changed += 1
            old["text"] = new["text"].strip()
        print(f"batch {start//BATCH+1}: ok ({len(batch)} blocks)")

    print(f"changed blocks: {changed}/{len(items)}")
    if not args.dry_run:
        Path(args.out).write_text(to_srt(items), encoding="utf-8")
        print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
