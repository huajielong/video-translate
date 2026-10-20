# -*- coding: utf-8 -*-
"""Build a human-readable QC summary from logs/*.qc.json.

  python make_report.py --logs DIR --out QC_SUMMARY.md
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    logs = Path(args.logs)
    seen = {}
    for p in sorted(logs.glob("*.qc.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        vp = d.get("video_path")
        name = Path(vp).name if isinstance(vp, str) and vp else (p.name[:-len(".qc.json")] + ".mp4")
        seen[name] = d

    rows = []
    for name, d in sorted(seen.items()):
        dur = d.get("duration", {})
        audio = d.get("audio", {})
        sub = d.get("subtitles", {})
        rows.append((
            name,
            f"{dur.get('output', 0):.1f}",
            f"{audio.get('sample_rate', '?')}",
            f"{d.get('loudness', {}).get('integrated_lufs', '?')}",
            f"{sub.get('blocks', '?')}",
            f"{sub.get('max_lines', '?')}",
            f"{sub.get('glossary_recall', 0):.0%}" if sub.get("glossary_recall") is not None else "-",
            "PASS" if d.get("ok") else "FAIL",
        ))

    lines = [
        "# 中文译制 QC 汇总",
        "",
        "| 文件 | 时长(s) | 采样率 | 响度(LUFS) | 字幕块 | 最大行数 | 术语命中 | 结果 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    lines += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} | {r[7]} |" for r in rows]
    lines += ["", f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
