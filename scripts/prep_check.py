#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Pre-dub gate: verify prep outputs before spending GPU time on dubbing.

For every video directory under ``<out>/<work_dir>/prep/<stem>/`` it checks:

  * ``<source>.srt``, ``<target>.srt`` and ``<target>.tts.srt`` all exist
    (a missing ``.tts.srt`` means the LLM resegment audit never finished);
  * glossary recall of the translated SRT (``qc.GLOSSARY_MIN_RECALL``);
  * no TTS unit longer than ``resegment_target_max_chars``.

Exit code is non-zero when any video fails, so a batch driver or a prep
sub-agent's claim of "done" can be verified mechanically.

Usage:
    python scripts/prep_check.py --out "D:/videos/course/_中文译制"
    python scripts/prep_check.py --manifest courses.json [--only 1,3]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import qc  # noqa: E402
from _winhide import utf8_stdio  # noqa: E402

utf8_stdio()

TAG = re.compile(r"<[^>]+>")


def read_config() -> dict:
    try:
        return json.loads((SKILL / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def work_dir_for(out: Path) -> Path:
    wd = Path(read_config().get("work_dir") or "_work").expanduser()
    return wd if wd.is_absolute() else out / wd


def parse_units(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    units = []
    for block in re.split(r"\n\s*\n", text):
        if "-->" not in block:
            continue
        lines = [ln for ln in block.splitlines() if ln.strip()]
        body = [ln for ln in lines if "-->" not in ln and not ln.strip().isdigit()]
        if body:
            units.append(TAG.sub("", " ".join(body)).strip())
    return units


def check_course(out: Path, glossary: str, source: str, target: str,
                 max_tts: int) -> tuple[dict, list[str]]:
    prep = work_dir_for(out) / "prep"
    stats = {"videos": 0, "ok": 0, "missing": 0, "low_recall": 0,
             "over": 0, "units": 0}
    issues: list[str] = []
    if not prep.is_dir():
        return stats, [f"missing prep dir: {prep}"]
    for d in sorted(p for p in prep.iterdir() if p.is_dir()):
        stats["videos"] += 1
        en = d / f"{source}.srt"
        zh = d / f"{target}.srt"
        tts = d / f"{target}.tts.srt"
        missing = [p.name for p in (en, zh, tts) if not p.is_file()]
        if missing:
            stats["missing"] += 1
            issues.append(f"MISSING {d.name}: {', '.join(missing)}")
            continue
        low = False
        try:
            r = qc.check_subtitles(str(zh), str(en), glossary,
                                   qc.MAX_CJK_PER_LINE)
            recall = r.get("glossary_recall")
            recall = 1.0 if recall is None else float(recall)
            if recall < qc.GLOSSARY_MIN_RECALL:
                low = True
                stats["low_recall"] += 1
                miss = "|".join(r.get("missing_terms") or [])
                issues.append(f"LOW {d.name}: recall={recall:.1%} missing={miss}")
        except Exception as exc:  # noqa: BLE001
            low = True
            stats["low_recall"] += 1
            issues.append(f"LOW {d.name}: glossary check failed: {exc}")
        units = parse_units(tts)
        stats["units"] += len(units)
        over = sum(1 for u in units if len(u) > max_tts)
        if over:
            stats["over"] += 1
            issues.append(f"OVER {d.name}: {over} units > {max_tts} chars")
        if not low and not over:
            stats["ok"] += 1
    return stats, issues


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--out", help="course output directory")
    src.add_argument("--manifest", help="batch manifest with course outputs")
    ap.add_argument("--only", default="",
                    help="comma-separated 1-based course indexes (manifest mode)")
    ap.add_argument("--source", default="", help="source lang (default from config)")
    ap.add_argument("--target", default="", help="target lang (default from config)")
    ap.add_argument("--glossary", default="", help="glossary file (default from config)")
    ap.add_argument("--max-tts-chars", type=int, default=0,
                    help="TTS unit char cap (default: resegment_target_max_chars)")
    args = ap.parse_args()

    cfg = read_config()
    source = args.source or cfg.get("source_lang") or "en"
    target = args.target or cfg.get("target_lang") or "zh-cn"
    max_tts = args.max_tts_chars or int(cfg.get("resegment_target_max_chars") or 44)
    glossary = Path(args.glossary or cfg.get("glossary") or "assets/glossary.txt")
    if not glossary.is_absolute():
        glossary = SKILL / glossary
    if not glossary.is_file():
        print(f"glossary not found: {glossary}")
        return 2

    courses: list[tuple[str, Path]] = []
    if args.out:
        courses.append(("", Path(args.out)))
    else:
        sys.path.insert(0, str(SCRIPTS))
        import batch_courses as bc  # noqa: PLC0415

        manifest = bc.load_manifest(Path(args.manifest))
        selected = ({int(x) for x in args.only.split(",") if x.strip()}
                    if args.only.strip() else None)
        for c in manifest["courses"]:
            if selected is None or c["index"] in selected:
                courses.append((f"#{c['index']} {c['name']}", Path(c["out"])))

    failed = 0
    for label, out in courses:
        stats, issues = check_course(out, str(glossary), source, target, max_tts)
        prefix = f"{label}: " if label else f"{out.name}: "
        print(f"{prefix}videos={stats['videos']} ok={stats['ok']} "
              f"missing={stats['missing']} low_recall={stats['low_recall']} "
              f"over{max_tts}={stats['over']} tts_units={stats['units']}")
        for line in issues:
            print(f"  {line}")
        if issues or stats["videos"] == 0:
            failed += 1
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
