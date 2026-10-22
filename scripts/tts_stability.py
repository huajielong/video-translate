#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Repeatable IndexTTS2 stability benchmark.

Runs N subtitle lines through the native /v1/tts API (no pyvideotrans cache),
optionally restarts the daemon every K lines, and samples CPU/GPU state plus
the daemon-side VTV_PROBE_FILE JSONL.

Example:
  python scripts/tts_stability.py --stem 002 \
      --src "D:/videos" --out "D:/translated" --blocks 100 \
      --restart-every 0 --runs 1

The daemon must have been started with VTV_PROBE_FILE set.  `pipeline.py
serve` now sets it from config (`"probe": true`); restart serve once after
upgrading so the daemon inherits it.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
TOOLS = SKILL / "tools"
LOGS = SKILL / ".runtime" / "logs"
DEFAULT_API = "http://127.0.0.1:8791"
IS_WINDOWS = platform.system().lower() == "windows"

sys.path.insert(0, str(SCRIPTS))
from _winhide import hide_console_windows  # noqa: E402

hide_console_windows()


def parse_srt(path: Path) -> list[dict]:
    """Parse SRT blocks into {start, end, text} milliseconds."""
    content = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    out = []
    for block in re.split(r"\n\s*\n", content):
        lines = [x.strip("﻿").strip() for x in block.splitlines() if x.strip()]
        if len(lines) < 3:
            continue
        m = re.match(
            r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)",
            lines[1])
        if not m:
            continue
        g = list(map(int, m.groups()))
        out.append({
            "start": g[0] * 3600000 + g[1] * 60000 + g[2] * 1000 + g[3],
            "end": g[4] * 3600000 + g[5] * 60000 + g[6] * 1000 + g[7],
            "text": "\n".join(lines[2:]).strip(),
        })
    return out


def find_srt(ns) -> Path:
    """Resolve the translated SRT for a stem (same layout as the pipeline)."""
    if getattr(ns, "zh", ""):
        return Path(ns.zh)
    out = Path(ns.out)
    stem, target = ns.stem, ns.target
    for p in (out / "_work" / "prep" / stem / f"{target}.srt",
              out / "_字幕" / f"{stem}.{target}.srt",
              out / f"{stem}.{target}.srt"):
        if p.is_file():
            return p
    raise SystemExit(f"translated SRT not found under {out} for {stem} ({target})")


def find_ffmpeg() -> str:
    cand = TOOLS / "ffmpeg" / "bin" / ("ffmpeg.exe" if IS_WINDOWS else "ffmpeg")
    if cand.exists():
        return str(cand)
    return shutil.which("ffmpeg") or "ffmpeg"


def find_nvidia_smi() -> str | None:
    exe = shutil.which("nvidia-smi")
    if exe:
        return exe
    if IS_WINDOWS:
        cand = Path(r"C:\Windows\System32\nvidia-smi.exe")
        if cand.exists():
            return str(cand)
    return None


def find_itt_cli(home: Path) -> str:
    p = home / "scripts" / ("indextts2.cmd" if IS_WINDOWS else "indextts2.sh")
    return str(p)


def srt_path(args) -> Path:
    if args.zh:
        return Path(args.zh)
    ns = argparse.Namespace(zh="", out=args.out, stem=args.stem, target=args.target)
    return find_srt(ns)


def make_ref(src: Path, out: Path, start: float = 10.0, seconds: float = 10.0) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        find_ffmpeg(), "-y", "-v", "error", "-ss", str(start), "-i", str(src),
        "-t", str(seconds), "-vn", "-ac", "1", "-ar", "24000",
        "-c:a", "pcm_s16le", str(out),
    ], check=True)
    return out


def daemon_state(api: str) -> str | None:
    try:
        with urllib.request.urlopen(api.rstrip("/") + "/health", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        return (data.get("daemon") or {}).get("state")
    except Exception:
        return None


def daemon_pid(api: str) -> int | None:
    try:
        with urllib.request.urlopen(api.rstrip("/") + "/health", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        return (data.get("daemon") or {}).get("pid")
    except Exception:
        return None


def wait_daemon_ready(api: str, timeout: float = 180.0) -> float:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if daemon_state(api) == "ready":
            return time.perf_counter() - t0
        time.sleep(0.5)
    raise RuntimeError(f"daemon not ready after {timeout}s")


def stop_daemon(cli: str, api: str) -> float:
    """Stop the daemon; the next /v1/tts request will cold-start it."""
    if not Path(cli).exists():
        raise RuntimeError(f"indextts2 CLI not found: {cli}")
    t0 = time.perf_counter()
    subprocess.run([cli, "stop", "--json"], capture_output=True, timeout=120)
    # Wait until the API stops reporting a ready daemon, then return.  The
    # next request pays the cold-start cost, which is what we want to measure.
    deadline = time.time() + 60
    while time.time() < deadline:
        if daemon_state(api) != "ready":
            break
        time.sleep(0.5)
    return time.perf_counter() - t0


def post_tts(api: str, text: str, ref: Path, output_name: str,
             timeout: float = 3600.0) -> tuple[float, dict, str]:
    payload = json.dumps({
        "text": text,
        "voice": str(ref),
        "output_name": output_name,
        "fast": True,
    }).encode("utf-8")
    last: Exception | None = None
    for attempt in range(8):
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(
                api.rstrip("/") + "/v1/tts", data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            wall = time.perf_counter() - t0
            out = data.get("output_path") or (data.get("result") or {}).get("output_path")
            if data.get("ok") is False or not out:
                raise RuntimeError(str(data)[:300])
            return wall, data, str(out)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < 7:
                time.sleep(5)
    raise RuntimeError(f"TTS failed: {last}")


def start_gpu_log(path: Path) -> subprocess.Popen | None:
    exe = find_nvidia_smi()
    if not exe:
        print("[monitor] nvidia-smi not found; GPU samples disabled")
        return None
    fields = (
        "timestamp,clocks.sm,clocks.max.sm,power.draw,enforced.power.limit,"
        "temperature.gpu,utilization.gpu,memory.used,pstate,"
        "clocks_throttle_reasons.active,clocks_throttle_reasons.hw_thermal_slowdown,"
        "clocks_throttle_reasons.sw_power_cap"
    )
    f = path.open("w", encoding="utf-8", newline="")
    f.write("timestamp,clocks_sm,clocks_max,power_w,power_limit_w,temp_c,util_pct,"
            "vram_mib,pstate,throttle,hw_thermal,sw_power_cap\n")
    f.flush()
    proc = subprocess.Popen(
        [exe, f"--query-gpu={fields}", "--format=csv,noheader,nounits", "-l", "1"],
        stdout=f, stderr=subprocess.STDOUT, text=True)
    proc._stability_file = f  # type: ignore[attr-defined]
    return proc


def start_cpu_log(path: Path) -> subprocess.Popen | None:
    counter = r"\Processor Information(_Total)\% Processor Performance"
    try:
        proc = subprocess.Popen(
            ["typeperf", counter, "-si", "1", "-sc", "100000", "-f", "CSV",
             "-o", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        return proc
    except Exception as exc:  # noqa: BLE001
        print(f"[monitor] typeperf unavailable: {exc}")
        return None


def stop_proc(proc: subprocess.Popen | None) -> None:
    if not proc:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True)
            else:
                proc.kill()
        except Exception:
            pass
    f = getattr(proc, "_stability_file", None)
    if f:
        try:
            f.close()
        except Exception:
            pass


def parse_gpu(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = [x.strip() for x in line.strip().split(",")]
            if len(parts) < 12 or parts[0] == "timestamp":
                continue
            try:
                ts = time.mktime(time.strptime(parts[0], "%Y/%m/%d %H:%M:%S.%f"))
            except Exception:
                continue
            def num(x):
                try:
                    return float(x)
                except Exception:
                    return None
            rows.append({
                "ts": ts,
                "clocks_sm": num(parts[1]),
                "clocks_max": num(parts[2]),
                "power_w": num(parts[3]),
                "power_limit_w": num(parts[4]),
                "temp_c": num(parts[5]),
                "util_pct": num(parts[6]),
                "vram_mib": num(parts[7]),
                "pstate": parts[8],
                "throttle": parts[9],
                "hw_thermal": parts[10],
                "sw_power_cap": parts[11],
            })
    return rows


def parse_cpu(path: Path) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r'^"([^"]+)","\s*([\d.]+)\s*"', line.strip())
            if not m:
                continue
            try:
                ts = time.mktime(time.strptime(m.group(1), "%m/%d/%Y %H:%M:%S.%f"))
                rows.append((ts, float(m.group(2))))
            except Exception:
                continue
    return rows


def read_probe(path: Path, offset: int) -> tuple[list[dict], int]:
    if not path.exists():
        return [], offset
    out: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        data = f.read()
        offset = f.tell()
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out, offset


def median(values):
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    mx = sum(x for x, _ in pairs) / len(pairs)
    my = sum(y for _, y in pairs) / len(pairs)
    cov = sum((x - mx) * (y - my) for x, y in pairs)
    vx = sum((x - mx) ** 2 for x, _ in pairs)
    vy = sum((y - my) ** 2 for _, y in pairs)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def window_medians(values, width=10):
    out = []
    for i in range(0, len(values), width):
        chunk = [v for v in values[i:i + width] if v is not None]
        out.append(round(statistics.median(chunk), 2) if chunk else None)
    return out


def summarize_run(lines: list[dict], probe_map: dict, gpu: list[dict],
                  cpu: list[tuple[float, float]], restart_every: int,
                  cold_starts: list[float], ac_state) -> dict:
    measured = [r for r in lines if r.get("ok") and not r.get("warmup")]
    walls = [r["wall"] for r in measured]
    gpts = [probe_map.get(r.get("output_name"), {}).get("gpt_gen") for r in measured]
    totals = [probe_map.get(r.get("output_name"), {}).get("total") for r in measured]
    s2 = [probe_map.get(r.get("output_name"), {}).get("s2mel") for r in measured]
    bg = [probe_map.get(r.get("output_name"), {}).get("bigvgan") for r in measured]
    reserved = [probe_map.get(r.get("output_name"), {}).get("cuda_reserved_mb")
                for r in measured]
    cuda_slope = None
    res_pairs = [(i, v) for i, v in enumerate(reserved) if v is not None]
    if len(res_pairs) >= 2:
        cuda_slope = (res_pairs[-1][1] - res_pairs[0][1]) / max(1, res_pairs[-1][0] - res_pairs[0][0])

    # Per-line GPU/CPU window medians.
    for r in measured:
        g = [s for s in gpu if r["t0"] <= s["ts"] <= r["t1"]]
        c = [v for ts, v in cpu if r["t0"] <= ts <= r["t1"]]
        r["gpu_clocks"] = median([s["clocks_sm"] for s in g])
        r["gpu_power"] = median([s["power_w"] for s in g])
        r["gpu_temp"] = median([s["temp_c"] for s in g])
        r["cpu_perf"] = median(c)
        r["gpu_throttle"] = any(
            (s.get("throttle") not in (None, "Not Active", "0x0000000000000000"))
            or s.get("hw_thermal") == "Active"
            or s.get("sw_power_cap") == "Active"
            for s in g)

    gpt_fast = [(r, probe_map.get(r.get("output_name"), {})) for r in measured
                if (probe_map.get(r.get("output_name"), {}).get("gpt_gen") or 99) <= 8]
    gpt_slow = [(r, probe_map.get(r.get("output_name"), {})) for r in measured
                if (probe_map.get(r.get("output_name"), {}).get("gpt_gen") or 0) >= 15]
    return {
        "ok_lines": len(measured),
        "failed_lines": len(lines) - len([r for r in lines if r.get("ok")]),
        "restart_every": restart_every,
        "restart_events": sum(1 for r in measured if r.get("cold_start")),
        "restart_stop_s": round(sum(cold_starts), 2),
        "wall_total_s": round(sum(walls), 2),
        "wall_median_s": round(statistics.median(walls), 2) if walls else None,
        "wall_p90_s": round(sorted(walls)[max(0, int(len(walls) * 0.9) - 1)], 2) if walls else None,
        "restart_wall_median_s": round(median([r["wall"] for r in measured
                                               if r.get("cold_start")]), 2)
        if median([r["wall"] for r in measured if r.get("cold_start")]) else None,
        "gpt_first10_median": round(median(gpts[:10]), 2) if median(gpts[:10]) else None,
        "gpt_last10_median": round(median(gpts[-10:]), 2) if median(gpts[-10:]) else None,
        "gpt_median": round(median(gpts), 2) if median(gpts) else None,
        "gpt_p90": round(sorted([x for x in gpts if x is not None])[int(len([x for x in gpts if x is not None]) * 0.9) - 1], 2)
        if any(x is not None for x in gpts) else None,
        "gpt_windows": window_medians(gpts),
        "s2mel_median": round(median(s2), 2) if median(s2) else None,
        "bigvgan_median": round(median(bg), 2) if median(bg) else None,
        "total_median": round(median(totals), 2) if median(totals) else None,
        "cuda_reserved_first": next((v for v in reserved if v is not None), None),
        "cuda_reserved_last": next((v for v in reversed(reserved) if v is not None), None),
        "cuda_reserved_slope_mb_per_line": round(cuda_slope, 3) if cuda_slope is not None else None,
        "fast_lines": len(gpt_fast),
        "slow_lines": len(gpt_slow),
        "gpu_clock_fast_median": round(median([r.get("gpu_clocks") for r, _ in gpt_fast]), 1)
        if median([r.get("gpu_clocks") for r, _ in gpt_fast]) else None,
        "gpu_clock_slow_median": round(median([r.get("gpu_clocks") for r, _ in gpt_slow]), 1)
        if median([r.get("gpu_clocks") for r, _ in gpt_slow]) else None,
        "cpu_perf_fast_median": round(median([r.get("cpu_perf") for r, _ in gpt_fast]), 1)
        if median([r.get("cpu_perf") for r, _ in gpt_fast]) else None,
        "cpu_perf_slow_median": round(median([r.get("cpu_perf") for r, _ in gpt_slow]), 1)
        if median([r.get("cpu_perf") for r, _ in gpt_slow]) else None,
        "gpu_temp_slow_median": round(median([r.get("gpu_temp") for r, _ in gpt_slow]), 1)
        if median([r.get("gpu_temp") for r, _ in gpt_slow]) else None,
        "throttle_lines": sum(1 for r in measured if r.get("gpu_throttle")),
        "corr_gpt_vs_gpu_clock": round(pearson(gpts, [r.get("gpu_clocks") for r in measured]), 3)
        if pearson(gpts, [r.get("gpu_clocks") for r in measured]) is not None else None,
        "corr_gpt_vs_cpu_perf": round(pearson(gpts, [r.get("cpu_perf") for r in measured]), 3)
        if pearson(gpts, [r.get("cpu_perf") for r in measured]) is not None else None,
        "corr_gpt_vs_cuda_reserved": round(pearson(gpts, reserved), 3)
        if pearson(gpts, reserved) is not None else None,
        "ac_power": ac_state,
        "daemon_pid_before": lines[0].get("daemon_pid") if lines else None,
        "daemon_pid_after": lines[-1].get("daemon_pid") if lines else None,
    }


def run_once(items, refs, args, api: str, run_id: str, result_dir: Path,
             probe_path: Path, cli: str, gpu_log: list[dict],
             cpu_log: list[tuple[float, float]], probe_offset: int):
    lines: list[dict] = []
    cold_starts: list[float] = []
    measured_idx = 0
    if args.fresh_daemon:
        try:
            cold_starts.append(stop_daemon(cli, api))
        except Exception as exc:  # noqa: BLE001
            print(f"[fresh] stop failed: {exc}")
    for i, it in enumerate(items):
        if i < args.warmup:
            warmup = True
        else:
            warmup = False
        ref = refs[i % len(refs)]
        output_name = f"stability_{run_id}_{i:04d}.wav"
        restart_before = False
        if args.restart_every and not warmup and measured_idx and measured_idx % args.restart_every == 0:
            try:
                cold_starts.append(stop_daemon(cli, api))
                restart_before = True
            except Exception as exc:  # noqa: BLE001
                print(f"[restart] failed: {exc}")
        t0 = time.time()
        record = {"i": i, "warmup": warmup, "ref": str(ref),
                  "output_name": output_name, "t0": t0,
                  "cold_start": restart_before,
                  "daemon_pid": daemon_pid(api)}
        try:
            _, data, out_path = post_tts(api, it["text"], ref, output_name)
            record.update({
                "ok": True, "wall": time.time() - t0, "t1": time.time(),
                "output_path": out_path,
                "output_name": Path(out_path).name,
                "audio_seconds": data.get("audio_seconds"),
            })
            if not warmup:
                measured_idx += 1
        except Exception as exc:  # noqa: BLE001
            record.update({"ok": False, "wall": time.time() - t0,
                           "t1": time.time(), "error": str(exc)[:400]})
            if not warmup:
                measured_idx += 1
        lines.append(record)
        gpt = "?"
        print(f"[{i + 1}/{len(items)}] {record['wall']:.1f}s ok={record['ok']} "
              f"restart_every={args.restart_every} gpt={gpt}")
    probe_rows, probe_offset = read_probe(probe_path, probe_offset)
    probe_map = {
        re.sub(r"\.partial-j_[0-9a-f]+", "", r.get("output", "")): r
        for r in probe_rows if r.get("output")
    }
    summary = summarize_run(lines, probe_map, gpu_log, cpu_log,
                            args.restart_every, cold_starts, args.ac_power)
    summary["run_id"] = run_id
    summary["probe_rows"] = len(probe_rows)
    summary["probe_matched"] = sum(1 for r in lines
                                   if r.get("output_name") in probe_map)
    return lines, summary, probe_offset, cold_starts


def main() -> int:
    ap = argparse.ArgumentParser(description="IndexTTS2 stability benchmark")
    ap.add_argument("--stem", required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--zh", default="")
    ap.add_argument("--target", default="zh-cn")
    ap.add_argument("--blocks", type=int, default=100)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--restart-every", type=int, default=0)
    ap.add_argument("--fresh-daemon", action="store_true",
                    help="stop the daemon before the run; warm-up pays the cold start")
    ap.add_argument("--ref-mode", choices=("fixed", "alternate"), default="fixed")
    ap.add_argument("--ref", default="")
    ap.add_argument("--ref2", default="")
    ap.add_argument("--index-api", default=DEFAULT_API)
    ap.add_argument("--itt-cli", default="")
    ap.add_argument("--probe", default=str(LOGS / "tts_probe.jsonl"))
    ap.add_argument("--result-dir", default="")
    ap.add_argument("--no-monitor", action="store_true")
    args = ap.parse_args()

    srt = srt_path(args)
    items = parse_srt(srt)[:args.blocks]
    if not items:
        print(f"no subtitle blocks in {srt}")
        return 2
    src = Path(args.src) / f"{args.stem}.mp4"
    if not src.exists():
        print(f"source not found: {src}")
        return 2

    result_dir = Path(args.result_dir) if args.result_dir else (
        SKILL / ".runtime" / "stability" / time.strftime("%Y%m%d_%H%M%S"))
    result_dir.mkdir(parents=True, exist_ok=True)

    refs: list[Path] = []
    if args.ref:
        refs.append(Path(args.ref))
    if args.ref2:
        refs.append(Path(args.ref2))
    if not refs:
        refs.append(make_ref(src, result_dir / "ref_a.wav", 10.0, 10.0))
        if args.ref_mode == "alternate":
            refs.append(make_ref(src, result_dir / "ref_b.wav", 30.0, 10.0))
    if args.ref_mode == "alternate" and len(refs) < 2:
        print("alternate ref mode needs --ref2 or a long enough source")
        return 2

    cli = args.itt_cli or find_itt_cli(SKILL / "vendor" / "index-tts2-cli")
    if args.restart_every and not Path(cli).exists():
        print(f"indextts2 CLI not found: {cli}")
        return 2

    probe_path = Path(args.probe)
    probe_offset = probe_path.stat().st_size if probe_path.exists() else 0
    if not probe_path.exists():
        print(f"[probe] not found: {probe_path}")
        print("[probe] restart `pipeline.py serve` after setting probe=true so the "
              "daemon inherits VTV_PROBE_FILE")
    else:
        print(f"[probe] {probe_path} (offset {probe_offset})")

    try:
        import pipeline as pl
        args.ac_power = pl._ac_power()
    except Exception:
        args.ac_power = None
    print(f"[power] AC={args.ac_power} (None=unknown)")

    gpu_proc = None
    cpu_proc = None
    gpu_path = result_dir / "gpu.csv"
    cpu_path = result_dir / "cpu.csv"
    if not args.no_monitor:
        gpu_proc = start_gpu_log(gpu_path)
        cpu_proc = start_cpu_log(cpu_path)

    all_lines: list[dict] = []
    summaries: list[dict] = []
    try:
        for run_no in range(args.runs):
            run_id = f"{time.strftime('%H%M%S')}_{run_no}"
            lines, summary, probe_offset, cold_starts = run_once(
                items, refs, args, args.index_api, run_id, result_dir,
                probe_path, cli, [], [], probe_offset)
            # Re-read the monitor logs each run so the per-line windows match.
            gpu_rows = parse_gpu(gpu_path) if gpu_proc else []
            cpu_rows = parse_cpu(cpu_path) if cpu_proc else []
            # Recompute with monitor data (run_once used empty lists).
            probe_rows, _ = read_probe(probe_path, 0)
            probe_map = {
                re.sub(r"\.partial-j_[0-9a-f]+", "", r.get("output", "")): r
                for r in probe_rows if r.get("output")
            }
            summary = summarize_run(lines, probe_map, gpu_rows, cpu_rows,
                                    args.restart_every, cold_starts, args.ac_power)
            summary["run_id"] = run_id
            summary["probe_matched"] = sum(1 for r in lines
                                           if r.get("output_name") in probe_map)
            all_lines.extend(lines)
            summaries.append(summary)
            print(f"[run {run_no}] {json.dumps(summary, ensure_ascii=False)}")
    finally:
        stop_proc(gpu_proc)
        stop_proc(cpu_proc)

    with (result_dir / "lines.jsonl").open("w", encoding="utf-8") as f:
        for r in all_lines:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (result_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summaries, f, ensure_ascii=False, indent=2)
    print(f"[done] {result_dir}")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0 if all(s.get("failed_lines", 0) == 0 for s in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
