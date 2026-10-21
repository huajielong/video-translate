#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Calibrate the dubbing-unit duration model from TTS probe records.

The fit stage predicts how long IndexTTS2 will take for a line:

    audio_seconds ~= fit_pred_a + fit_pred_b * chinese_chars

Defaults (1.30s, 0.123s/char) were fitted on Chinese IndexTTS2 with beam=1.
Re-run this after changing the TTS backend, voice, language, or beam size,
then put the printed values into config.json.

Usage:
  python scripts/fit_calibrate.py
  python scripts/fit_calibrate.py --probe .runtime/logs/tts_probe.jsonl --last 232
  python scripts/fit_calibrate.py --pid 14080 --json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

DEFAULT_PROBE = Path(__file__).resolve().parent.parent / ".runtime" / "logs" / "tts_probe.jsonl"


def load_rows(path: Path, pid: int | None, last: int) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if pid is not None and row.get("pid") != pid:
                continue
            if not row.get("chars") or not row.get("audio"):
                continue
            rows.append(row)
    return rows[-last:] if last > 0 else rows


def fit(rows: list[dict]) -> dict:
    xs = [float(r["chars"]) for r in rows]
    ys = [float(r["audio"]) for r in rows]
    n = len(xs)
    if n < 2:
        raise SystemExit("need at least 2 valid probe rows")
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 0:
        raise SystemExit("all rows have the same character count; need more variety")
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    a = my - b * mx
    errors = sorted(abs(a + b * x - y) for x, y in zip(xs, ys))
    return {
        "rows": n,
        "fit_pred_a": round(a, 3),
        "fit_pred_b": round(b, 4),
        "chars_per_second": round(1.0 / b, 2) if b > 0 else None,
        "mae_s": round(sum(errors) / n, 3),
        "median_abs_error_s": round(statistics.median(errors), 3),
        "p90_abs_error_s": round(errors[int(n * 0.9)], 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe", default=str(DEFAULT_PROBE),
                    help="tts_probe.jsonl written by the IndexTTS2 daemon")
    ap.add_argument("--pid", type=int, default=0,
                    help="only use rows from this daemon pid (0 = all)")
    ap.add_argument("--last", type=int, default=0,
                    help="use only the last N valid rows (0 = all)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    path = Path(args.probe)
    if not path.is_file():
        print(f"probe not found: {path}", file=sys.stderr)
        return 2
    result = fit(load_rows(path, args.pid or None, args.last))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"rows={result['rows']}  "
          f"a={result['fit_pred_a']:.2f}s  b={result['fit_pred_b']:.3f}s/char  "
          f"(~{result['chars_per_second']} chars/s asymptotic)")
    print(f"MAE={result['mae_s']:.3f}s  median={result['median_abs_error_s']:.3f}s  "
          f"P90={result['p90_abs_error_s']:.3f}s")
    print("\nconfig.json:")
    print(f'  "fit_pred_a": {result["fit_pred_a"]},')
    print(f'  "fit_pred_b": {result["fit_pred_b"]},')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
