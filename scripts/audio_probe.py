#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Locate a bad TTS sample by its high-band energy, and prove a rebuild fixed it.

The pipeline trims every TTS line to its first/last non-silent sample, fades
20ms and adds 80ms of digital silence, which removes the boundary "whoosh".
IndexTTS2 can still emit an isolated 1-2ms broadband pulse *inside* a word.
The dubbing cache key does not include the random seed, so that wav is frozen
and every rebuild copies it back.

Listening is not enough to tell a bad pulse from a normal plosive (p/t/k) or
fricative (x/ɕ/s/sh), and neither is a single energy number: normal speech has
short peaks too.  The reliable test is a rebuild A/B -- clear the dubbing cache,
synthesize again, and compare the same window:

  # 1. where is the energy?  (candidates, normal consonants included)
  python scripts/audio_probe.py out/007_中文.mp4 --start 0.8 --end 1.0

  # 2. did the rebuild remove it?  ratio > 1 means the first file was louder
  python scripts/audio_probe.py out/007_中文.mp4 --compare /tmp/old.mp4 \\
      --start 0.8 --end 1.0

A real bad sample drops by 10x-100x at exactly one 1ms window while its
neighbours stay put; a plosive that exists in both takes does not move.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from array import array
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
TOOLS = SKILL / "tools"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _winhide import hide_console_windows  # noqa: E402

hide_console_windows()

BAND_LO = 4000
BAND_HI = 10000
SAMPLE_RATE = 48000
WIN_MS = 1.0
RADIUS_MS = 25.0     # local background window on each side
RATIO = 20.0         # peak / local median before a window is a candidate
MIN_LEVEL = 1e-3     # ignore anything quieter than this band RMS
MAX_DUR_MS = 4.0     # longer energy runs are speech, not an isolated pulse
TOP = 10


def ffmpeg_bin() -> str:
    for name in ("ffmpeg", "ffmpeg.exe"):
        cand = TOOLS / "ffmpeg" / "bin" / name
        if cand.exists():
            return str(cand)
    return shutil.which("ffmpeg") or "ffmpeg"


def decode_band(path, start: float = 0.0, duration: float | None = None,
                sr: int = SAMPLE_RATE, lo: int = BAND_LO,
                hi: int = BAND_HI) -> array:
    """Decode mono float32 with a 4-10 kHz band-pass applied by ffmpeg."""
    cmd = [ffmpeg_bin(), "-v", "error", "-i", str(path)]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    if duration and duration > 0:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr),
            "-af", f"highpass=f={lo},lowpass=f={hi}",
            "-f", "f32le", "-"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(f"ffmpeg failed: {msg[-1] if msg else 'unknown error'}")
    samples = array("f")
    samples.frombytes(proc.stdout)
    return samples


def window_rms(samples: array, sr: int, win_ms: float = WIN_MS) -> list[float]:
    """RMS per window; one value per ``win_ms`` of audio."""
    win = max(1, int(round(sr * win_ms / 1000.0)))
    out = []
    total = len(samples) - len(samples) % win
    for i in range(0, total, win):
        acc = 0.0
        for x in samples[i:i + win]:
            acc += x * x
        out.append(math.sqrt(acc / win))
    return out


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


def _local_background(levels: list[float], i: int, radius: int) -> float:
    lo, hi = max(0, i - radius), min(len(levels), i + radius + 1)
    return _median(levels[lo:hi]) or 0.0


def find_peaks(levels: list[float], hop_ms: float = WIN_MS,
               radius_ms: float = RADIUS_MS, ratio: float = RATIO,
               min_level: float = MIN_LEVEL,
               max_dur_ms: float = MAX_DUR_MS) -> list[dict]:
    """Short windows far above their neighbours, longest-lived merged.

    Normal plosives and fricatives show up here too; this locates candidates,
    it does not decide whether one is a defect.  Use :func:`compare_levels`
    after a rebuild for that.
    """
    n = len(levels)
    if n == 0:
        return []
    radius = max(1, int(round(radius_ms / hop_ms)))
    hits = []
    for i, v in enumerate(levels):
        if v < min_level:
            continue
        bg = _local_background(levels, i, radius) or min_level
        if v >= ratio * bg:
            hits.append(i)

    events = []
    for i in hits:
        if events and i - events[-1][1] <= 1:
            events[-1][1] = i
        else:
            events.append([i, i])

    found = []
    for a, b in events:
        dur_ms = (b - a + 1) * hop_ms
        if dur_ms > max_dur_ms:
            continue  # speech, not an isolated pulse
        peak = max(range(a, b + 1), key=lambda k: levels[k])
        bg = _local_background(levels, peak, radius) or min_level
        found.append({
            "time_s": round(peak * hop_ms / 1000.0, 3),
            "level": round(levels[peak], 6),
            "background": round(bg, 6),
            "ratio": round(levels[peak] / bg, 1),
            "duration_ms": round(dur_ms, 1),
        })
    found.sort(key=lambda c: -c["level"])
    return found


def compare_levels(before: list[float], after: list[float],
                   hop_ms: float = WIN_MS, min_level: float = MIN_LEVEL,
                   min_ratio: float = 10.0) -> list[dict]:
    """Windows that got much quieter after a rebuild.

    A cached bad sample disappears after clearing the dubbing cache, so its
    window drops by an order of magnitude while neighbours stay put.  Speech
    present in both takes barely moves (wording changes aside).
    """
    n = min(len(before), len(after))
    drops = []
    for i in range(n):
        b, a = before[i], after[i]
        if b < min_level:
            continue
        if a <= 0:
            a = 1e-9
        if b / a >= min_ratio:
            drops.append({
                "time_s": round(i * hop_ms / 1000.0, 3),
                "before": round(b, 6),
                "after": round(a, 6),
                "drop": round(b / a, 1),
            })
    drops.sort(key=lambda d: -d["drop"])
    return drops


def probe(path, start: float = 0.0, end: float = 0.0) -> list[float]:
    duration = end - start if end > start else None
    return window_rms(decode_band(path, start, duration), SAMPLE_RATE)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Locate suspect TTS pulses by 4-10 kHz band energy, "
                    "or compare two takes after a cache-cleared rebuild.")
    ap.add_argument("file")
    ap.add_argument("--compare", help="second file (e.g. the rebuilt take)")
    ap.add_argument("--start", type=float, default=0.0, help="start seconds")
    ap.add_argument("--end", type=float, default=0.0,
                    help="end seconds (0 = end of file)")
    ap.add_argument("--top", type=int, default=TOP,
                    help=f"show this many loudest candidates (default {TOP})")
    ap.add_argument("--radius-ms", type=float, default=RADIUS_MS)
    ap.add_argument("--ratio", type=float, default=RATIO)
    ap.add_argument("--min-level", type=float, default=MIN_LEVEL)
    ap.add_argument("--max-dur-ms", type=float, default=MAX_DUR_MS)
    ap.add_argument("--csv", help="write time,level (1ms windows) to a CSV")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--fail-on-click", action="store_true",
                    help="exit 3 when candidates are found")
    args = ap.parse_args(argv)

    levels = probe(args.file, args.start, args.end)
    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as fh:
            fh.write("time_s,band_rms\n")
            for i, v in enumerate(levels):
                fh.write(f"{args.start + i * WIN_MS / 1000.0:.3f},{v:.8f}\n")

    region = f"{args.start:.3f}s" if args.end <= args.start else \
        f"{args.start:.3f}-{args.end:.3f}s"

    if args.compare:
        other = probe(args.compare, args.start, args.end)
        n = min(len(levels), len(other))
        if abs(len(levels) - len(other)) > 50:
            print(f"warning: takes differ in length ({len(levels)} vs "
                  f"{len(other)} windows); comparing the first {n}",
                  file=sys.stderr)
        drops = compare_levels(levels[:n], other[:n])
        if args.json:
            print(json.dumps({"file": args.file, "compare": args.compare,
                              "region": region, "drops": drops[:args.top]},
                             ensure_ascii=False, indent=2))
        else:
            print(f"{args.file}  vs  {args.compare}  {region}  "
                  f"{n} x {WIN_MS:g}ms windows (band {BAND_LO}-{BAND_HI} Hz)")
            if not drops:
                print("no window differs by >= 10x; nothing points at a "
                      "bad cached sample")
            for d in drops[:args.top]:
                print(f"  {args.start + d['time_s']:.3f}s  "
                      f"{d['before']:.2e} -> {d['after']:.2e}  "
                      f"({d['drop']:.0f}x quieter)")
            if drops:
                print("  -> the old take had a pulse here; the rebuild "
                      "replaced the cached wav")
        if args.fail_on_click and drops:
            return 3
        return 0

    peaks = find_peaks(levels, radius_ms=args.radius_ms, ratio=args.ratio,
                       min_level=args.min_level,
                       max_dur_ms=args.max_dur_ms)
    if args.json:
        print(json.dumps({"file": args.file, "region": region,
                          "windows": len(levels), "candidates": peaks[:args.top]},
                         ensure_ascii=False, indent=2))
    else:
        print(f"{args.file}  {region}  {len(levels)} x {WIN_MS:g}ms windows "
              f"(band {BAND_LO}-{BAND_HI} Hz)")
        if not peaks:
            print("no short high-band pulses above the local background")
        else:
            print("candidates (loudest first; normal plosives look like this "
                  "too):")
            for c in peaks[:args.top]:
                print(f"  {args.start + c['time_s']:.3f}s  "
                      f"level {c['level']:.2e}  bg {c['background']:.2e}  "
                      f"{c['ratio']:.0f}x  {c['duration_ms']:.0f}ms")
            print("  -> confirm with --compare after clearing the TTS dubbing "
                  "cache and rebuilding\n"
                  "     (see references/troubleshooting.md)")
    if args.fail_on_click and peaks:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
