# -*- coding: utf-8 -*-
"""A/B harness: IndexTTS2 vs Qwen3-TTS on the same subtitle blocks.

Run one backend at a time (IndexTTS2 uses ~7.6 GB, Qwen ~3-4 GB):

  python ab.py --mode index   --stem NAME --src DIR --out DIR --blocks 30
  python ab.py --mode qwenx   --stem NAME --src DIR --out DIR --blocks 30
  python ab.py --mode qwenicl --stem NAME --src DIR --out DIR --blocks 30
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
QWEN_DEFAULT = SKILL / "vendor/pyvideotrans/models/models--Qwen--Qwen3-TTS-12Hz-0.6B-Base"


def parse_srt(path: Path):
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip())
    out = []
    for b in blocks:
        lines = [x.strip() for x in b.splitlines() if x.strip()]
        if len(lines) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[1])
        if not m:
            continue
        g = list(map(int, m.groups()))
        start = g[0] * 3600000 + g[1] * 60000 + g[2] * 1000 + g[3]
        end = g[4] * 3600000 + g[5] * 60000 + g[6] * 1000 + g[7]
        text = "\n".join(lines[2:]).strip()
        if text:
            out.append({"text": text, "start": start, "end": end})
    return out


def find_srt(args) -> Path:
    if args.zh:
        return Path(args.zh)
    out = Path(args.out)
    for p in (out / "_work" / "prep" / args.stem / f"{args.target}.srt",
              out / "_work" / args.stem / f"{args.target}.srt",
              out / "prep" / args.stem / f"{args.target}.srt"):
        if p.exists():
            return p
    raise SystemExit(f"no {args.target}.srt for {args.stem}; pass --zh")


def make_ref(args, out: Path, seconds: int = 10) -> Path:
    src = Path(args.src) / f"{args.stem}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "10", "-i", str(src),
                    "-t", str(seconds), "-vn", "-ac", "1", "-ar", "24000",
                    "-c:a", "pcm_s16le", str(out)], check=True)
    return out


def summarize(name: str, rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("ok")]
    walls = [r["wall"] for r in ok]
    ratios = [r["audio"] / r["target"] for r in ok if r.get("target")]
    total_audio = sum(r["audio"] for r in ok)
    total_wall = sum(walls)
    return {
        "variant": name,
        "lines": len(rows),
        "ok": len(ok),
        "failed": len(rows) - len(ok),
        "rtf": (total_wall / total_audio) if total_audio else None,
        "total_wall_s": round(total_wall, 2),
        "total_audio_s": round(total_audio, 2),
        "median_line_s": round(statistics.median(walls), 2) if walls else None,
        "p95_line_s": round(sorted(walls)[int(len(walls) * 0.95) - 1], 2) if walls else None,
        "duration_ratio_median": round(statistics.median(ratios), 3) if ratios else None,
        "pct_need_speedup_115": round(100 * sum(1 for r in ratios if r > 1.15) / len(ratios), 1) if ratios else None,
        "rows": rows,
    }


def run_index(items, ref: Path, outdir: Path, fast: bool, api: str) -> dict:
    import requests
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, it in enumerate(items):
        t0 = time.perf_counter()
        payload = {"text": it["text"], "voice": str(ref),
                   "output_name": f"index_{i:03d}.wav"}
        if fast:
            payload["fast"] = True
        try:
            r = requests.post(f"{api}/v1/tts", json=payload, timeout=3600)
            j = r.json()
        except Exception as exc:  # noqa: BLE001
            rows.append({"i": i, "ok": False, "error": str(exc)[:300],
                         "wall": time.perf_counter() - t0})
            continue
        if not j.get("ok"):
            rows.append({"i": i, "ok": False, "error": str(j)[:300],
                         "wall": time.perf_counter() - t0})
            continue
        wall = time.perf_counter() - t0
        rows.append({"i": i, "ok": True, "wall": wall,
                     "audio": float(j.get("audio_seconds") or 0),
                     "target": (it["end"] - it["start"]) / 1000.0})
    return summarize("index_fast" if fast else "index_beam3", rows)


def run_qwen(items, ref: Path, outdir: Path, xvec: bool, qwen_base: str) -> dict:
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel
    outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(qwen_base, device_map="cuda:0", dtype="auto")
    load_s = time.perf_counter() - t0
    rows = []
    prompt = None
    for i, it in enumerate(items):
        t1 = time.perf_counter()
        try:
            if prompt is None:
                prompt = model.create_voice_clone_prompt(
                    ref_audio=str(ref), ref_text=None, x_vector_only_mode=xvec)
            wavs, sr = model.generate_voice_clone(
                text=it["text"], language="Chinese",
                voice_clone_prompt=prompt, non_streaming_mode=True)
            wall = time.perf_counter() - t1
            out = outdir / f"qwen_{i:03d}.wav"
            sf.write(str(out), wavs[0], sr)
            rows.append({"i": i, "ok": True, "wall": wall,
                         "audio": len(wavs[0]) / sr,
                         "target": (it["end"] - it["start"]) / 1000.0})
        except Exception as exc:  # noqa: BLE001
            rows.append({"i": i, "ok": False, "error": str(exc)[:300],
                         "wall": time.perf_counter() - t1})
    result = summarize("qwen_xvec" if xvec else "qwen_icl", rows)
    result["model_load_s"] = load_s
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", required=True)
    ap.add_argument("--mode", required=True,
                    choices=["index", "indexfast", "qwenx", "qwenicl"])
    ap.add_argument("--blocks", type=int, default=30)
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--target", default="zh-cn")
    ap.add_argument("--zh", default="")
    ap.add_argument("--ref", default="")
    ap.add_argument("--index-api", default="http://127.0.0.1:8791")
    ap.add_argument("--qwen-base", default=str(QWEN_DEFAULT))
    args = ap.parse_args()

    srt = find_srt(args)
    items = parse_srt(srt)[:args.blocks]
    if not items:
        raise SystemExit("no subtitle blocks")
    outdir = Path(args.out) / "ab" / args.stem[:40] / args.mode
    ref = Path(args.ref) if args.ref else make_ref(args, Path(args.out) / "ab" / "_ref.wav")
    print(f"{args.mode}: {len(items)} blocks, ref={ref}")

    if args.mode == "index":
        res = run_index(items, ref, outdir, fast=False, api=args.index_api)
    elif args.mode == "indexfast":
        res = run_index(items, ref, outdir, fast=True, api=args.index_api)
    else:
        res = run_qwen(items, ref, outdir, xvec=(args.mode == "qwenx"),
                       qwen_base=args.qwen_base)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics.json").write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "rows"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
