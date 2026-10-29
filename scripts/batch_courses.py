#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Batch-translate multiple courses: parallel prep, serial dub+post.

`prep` runs one `pipeline.py prep` per course in parallel (prep never touches
the TTS service).  `run` executes `pipeline.py run` strictly one course at a
time because IndexTTS2 has a single worker; it archives each course's QC JSONs
before the next course can overwrite same-named stems, and writes status and
summary files.

Manifest (JSON, UTF-8):

    {
      "source": "en",
      "target": "zh-cn",
      "backend": "index",
      "state_dir": "D:/work/_batch",
      "qc_dir": "D:/work/_batch/qc",
      "courses": [
        {"name": "course-a", "root": "D:/videos", "modules": ["M1_Module 1"]},
        {"name": "course-b", "root": "D:/videos",
         "modules": ["M1_Module 1", "M2_Module 2"],
         "out": "D:/videos/course-b/_中文译制"}
      ]
    }

Relative `root` / `out` / `state_dir` / `qc_dir` resolve against the manifest
directory.  `out` defaults to `<root>/<name>/_中文译制`; `qc_dir` defaults to
`<state_dir>/qc` (set it to an existing archive such as `qc_d` when resuming a
historical batch, otherwise same-named stems from other courses hide its QC).

Usage:
    python scripts/batch_courses.py prep  --manifest courses.json [--workers 4]
    python scripts/batch_courses.py run   --manifest courses.json [--only 1,3]
    python scripts/batch_courses.py status --manifest courses.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
PIPELINE = SKILL / "scripts" / "pipeline.py"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_config() -> dict:
    try:
        return json.loads((SKILL / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    base = path.resolve().parent
    courses = data.get("courses") or []
    if not courses:
        raise SystemExit("manifest has no courses")
    for i, c in enumerate(courses, 1):
        c["index"] = i
        root = Path(c.get("root") or ".").expanduser()
        if not root.is_absolute():
            root = (base / root).resolve()
        c["root"] = str(root)
        out = c.get("out")
        if out:
            out = Path(out).expanduser()
            if not out.is_absolute():
                out = (base / out).resolve()
        else:
            out = root / c["name"] / "_中文译制"
        c["out"] = str(out)
        c["modules"] = list(c.get("modules") or ["M1_Module 1"])
    state = Path(data.get("state_dir") or (base / "_batch")).expanduser()
    if not state.is_absolute():
        state = (base / state).resolve()
    data["state_dir"] = str(state)
    qc = Path(data.get("qc_dir") or (state / "qc")).expanduser()
    if not qc.is_absolute():
        qc = (base / qc).resolve()
    data["qc_dir"] = str(qc)
    data.setdefault("source", "en")
    data.setdefault("target", "zh-cn")
    data.setdefault("backend", "index")
    return data


def log_line(state_dir: Path, message: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "batch.log").open("a", encoding="utf-8") as fh:
        fh.write(f"[{now()}] {message}\n")


def run_pipeline(args: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"\n===== {now()} pipeline {' '.join(args)} =====\n")
        fh.flush()
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        cp = subprocess.run(
            [sys.executable, str(PIPELINE), *args],
            cwd=str(SKILL),
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    return cp.returncode


def module_dir(course: dict, module: str) -> Path:
    return Path(course["root"]) / course["name"] / module


def stems(src: Path) -> list[str]:
    return [p.stem for p in sorted(src.glob("*.mp4"))] if src.is_dir() else []


def course_stems(course: dict) -> list[tuple[str, str]]:
    out = []
    for mod in course["modules"]:
        for stem in stems(module_dir(course, mod)):
            out.append((mod, stem))
    return out


def qc_ok(out: Path, stem: str, archived: Path | None = None) -> bool:
    """Check QC for one stem.  Prefer this course's archived JSON: the shared
    runtime log is overwritten by later courses that reuse the same stem."""
    p = archived / f"{stem}.qc.json" if archived else None
    if p is None or not p.is_file():
        p = SKILL / ".runtime" / "logs" / f"{stem}.qc.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    vp = d.get("video_path")
    if not isinstance(vp, str):
        return False
    try:
        return Path(vp).parent.resolve() == out.resolve() and bool(d.get("ok"))
    except Exception:
        return False


def pending_by_module(course: dict, suffix: str,
                      qc_root: Path | None = None) -> dict[str, list[str]]:
    out = Path(course["out"])
    archived = (qc_root / f"{course['index']:02d}_{course['name']}"
                if qc_root else None)
    pending: dict[str, list[str]] = {}
    for mod in course["modules"]:
        src = module_dir(course, mod)
        todo = [s for s in stems(src)
                if not (out / f"{s}{suffix}.mp4").is_file()
                or not qc_ok(out, s, archived)]
        if todo:
            pending[mod] = todo
    return pending


def prep_course(manifest: dict, course: dict, workers: int, logs: Path) -> dict:
    """Run prep for every module of one course (sequential within the course)."""
    rc_all = 0
    mods = []
    for mod in course["modules"]:
        src = module_dir(course, mod)
        if not src.is_dir():
            rc_all = 1
            mods.append({"module": mod, "rc": 1, "error": "missing module dir"})
            continue
        log = logs / f"prep_{course['index']:02d}_{course['name']}.log"
        rc = run_pipeline([
            "prep", "--src", str(src), "--out", course["out"],
            "--source", manifest["source"], "--target", manifest["target"],
            "--mt-workers", str(workers),
        ], log)
        rc_all = rc_all or rc
        mods.append({"module": mod, "rc": rc,
                     "videos": len(stems(src))})
    return {"index": course["index"], "course": course["name"],
            "modules": mods, "rc": rc_all}


def write_status(state_dir: Path, items: list[dict]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = state_dir / "batch_status.json.tmp"
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(state_dir / "batch_status.json")


def copy_course_qc(course: dict, qc_root: Path) -> Path:
    dest = qc_root / f"{course['index']:02d}_{course['name']}"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    out = Path(course["out"])
    for _, stem in course_stems(course):
        s = SKILL / ".runtime" / "logs" / f"{stem}.qc.json"
        if not s.is_file():
            continue
        try:
            d = json.loads(s.read_text(encoding="utf-8"))
            vp = d.get("video_path")
            if not isinstance(vp, str) or Path(vp).parent.resolve() != out.resolve():
                continue
        except Exception:
            continue
        shutil.copy2(s, dest / f"{stem}.qc.json")
    return dest


def qc_counts(qcdir: Path) -> tuple[int, int]:
    total = passed = 0
    for p in qcdir.glob("*.qc.json"):
        total += 1
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("ok"):
                passed += 1
        except Exception:
            pass
    return passed, total


def cmd_prep(manifest: dict, args) -> int:
    state_dir = Path(manifest["state_dir"])
    logs = state_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_line(state_dir, f"prep start ({len(manifest['courses'])} courses, workers={args.workers})")
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(prep_course, manifest, c, args.mt_workers, logs): c
                   for c in manifest["courses"]}
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                item = fut.result()
            except Exception as exc:  # noqa: BLE001
                item = {"index": c["index"], "course": c["name"], "rc": 1,
                        "error": repr(exc)}
            results.append(item)
            write_status(state_dir, sorted(results, key=lambda r: r["index"]))
            log_line(state_dir, f"prep done #{c['index']} {c['name']} rc={item['rc']}")
    log_line(state_dir, "prep finished")
    for r in sorted(results, key=lambda x: x["index"]):
        print(f"#{r['index']} {r['course']}: rc={r['rc']}")
    return 0 if all(r["rc"] == 0 for r in results) else 1


def cmd_run(manifest: dict, args) -> int:
    state_dir = Path(manifest["state_dir"])
    qc_root = Path(manifest["qc_dir"])
    logs = state_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    suffix = read_config().get("final_suffix", "_中文")
    selected = ({int(x) for x in args.only.split(",") if x.strip()}
                if args.only.strip() else {c["index"] for c in manifest["courses"]})
    log_line(state_dir, "run start")

    serve_rc = run_pipeline(["serve"], logs / "serve.log")
    if serve_rc != 0:
        log_line(state_dir, f"serve failed rc={serve_rc}; aborting")
        return serve_rc

    results = []
    for course in manifest["courses"]:
        if course["index"] not in selected:
            continue
        out = Path(course["out"])
        log = logs / f"run_{course['index']:02d}_{course['name']}.log"
        started = now()
        attempts = 0
        rc = 0
        missing_mods = [m for m in course["modules"]
                        if not module_dir(course, m).is_dir()]
        if missing_mods:
            log_line(state_dir, f"skip {course['name']}: missing {missing_mods}")
            rc = 1
        for attempt in (1, 2):
            pending = pending_by_module(course, suffix, qc_root)
            if not pending:
                break
            attempts = max(attempts, attempt)
            log_line(state_dir, f"#{course['index']} {course['name']} attempt {attempt}: "
                                f"{sum(len(v) for v in pending.values())} pending")
            for mod, videos in pending.items():
                cmd = ["run", "--src", str(module_dir(course, mod)),
                       "--out", str(out), "--source", manifest["source"],
                       "--target", manifest["target"], "--backend", manifest["backend"],
                       "--videos", ",".join(videos)]
                if args.allow_battery:
                    cmd.append("--allow-battery")
                rc = run_pipeline(cmd, log)
            if attempt == 1 and pending_by_module(course, suffix, qc_root):
                time.sleep(60)

        pending_all = [s for todo in pending_by_module(course, suffix, qc_root).values()
                       for s in todo]
        qcdir = copy_course_qc(course, qc_root)
        qc_passed, qc_total = qc_counts(qcdir)
        if qc_total:
            run_pipeline(["report", "--out", str(out), "--logs", str(qcdir)],
                         logs / f"report_{course['index']:02d}.log")
        finals = sum(1 for _, s in course_stems(course)
                     if (out / f"{s}{suffix}.mp4").is_file())
        item = {
            "index": course["index"], "course": course["name"], "out": str(out),
            "attempts": attempts, "rc": rc, "pending": pending_all,
            "ok": (not missing_mods and not pending_all
                   and qc_total > 0 and qc_passed == qc_total),
            "outputs": finals, "expected": len(course_stems(course)),
            "qc_passed": qc_passed, "qc_total": qc_total,
            "started": started, "finished": now(),
        }
        results.append(item)
        write_status(state_dir, results)
        log_line(state_dir, f"done #{course['index']} {course['name']}: "
                            f"{finals}/{item['expected']} outputs, "
                            f"qc {qc_passed}/{qc_total}")

    if not args.keep_serve:
        run_pipeline(["serve", "--stop"], logs / "serve_stop.log")

    lines = ["# 批量译制结果", "",
             "| # | 课程 | 成品 | QC | attempts | 结束 |",
             "|---:|---|---:|---:|---:|---|"]
    for r in results:
        lines.append(f"| {r['index']} | {r['course']} | {r['outputs']}/{r['expected']} | "
                     f"{r['qc_passed']}/{r['qc_total']} | {r['attempts']} | {r['finished']} |")
    lines.append("")
    (state_dir / "batch_summary.md").write_text("\n".join(lines), encoding="utf-8")
    log_line(state_dir, "run finished")
    return 0 if all(r["ok"] for r in results) else 1


def cmd_status(manifest: dict, _args) -> int:
    suffix = read_config().get("final_suffix", "_中文")
    qc_root = Path(manifest["qc_dir"])
    for course in manifest["courses"]:
        out = Path(course["out"])
        total = len(course_stems(course))
        finals = sum(1 for _, s in course_stems(course)
                     if (out / f"{s}{suffix}.mp4").is_file())
        pending = sum(len(v) for v in
                      pending_by_module(course, suffix, qc_root).values())
        print(f"#{course['index']} {course['name']}: {finals}/{total} finals, "
              f"{pending} pending")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    for name in ("prep", "run", "status"):
        p = sub.add_parser(name)
        p.add_argument("--manifest", required=True)
        if name == "prep":
            p.add_argument("--workers", type=int, default=4,
                           help="parallel courses (default 4)")
            p.add_argument("--mt-workers", type=int, default=2,
                           help="translation workers per course (default 2)")
        if name == "run":
            p.add_argument("--only", default="",
                           help="comma-separated 1-based course indexes")
            p.add_argument("--keep-serve", action="store_true")
            p.add_argument("--allow-battery", action="store_true")

    args = ap.parse_args()
    manifest = load_manifest(Path(args.manifest))
    if args.command == "prep":
        return cmd_prep(manifest, args)
    if args.command == "run":
        return cmd_run(manifest, args)
    return cmd_status(manifest, args)


if __name__ == "__main__":
    raise SystemExit(main())
