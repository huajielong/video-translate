#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""video-translate: portable orchestration for subtitled/dubbed video translation.

Stages: ASR -> translation -> voice-clone dubbing -> align/mux -> loudnorm -> QC.

Examples:
  python scripts/pipeline.py setup
  python scripts/pipeline.py doctor
  python scripts/pipeline.py serve
  python scripts/pipeline.py run --src "D:/videos" --out "D:/translated" \
      --source en --target zh-cn --backend index
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _winhide import ensure_stdio, hide_console_windows  # noqa: E402

hide_console_windows()

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
VENDOR = SKILL / "vendor"
PVT_DEFAULT = VENDOR / "pyvideotrans"
ITT_DEFAULT = VENDOR / "index-tts2-cli"
TOOLS = SKILL / "tools"
CONFIG = SKILL / "config.json"
RUNTIME = SKILL / ".runtime"
LOGS = RUNTIME / "logs"
STATE = RUNTIME / "state"
WRAPPER = SCRIPTS / "trans_cli.py"
SHIM = SCRIPTS / "gradio_shim.py"
QC = SCRIPTS / "qc.py"
POLISH = SCRIPTS / "polish_srt.py"
REPORT = SCRIPTS / "make_report.py"
SEGMENT = SCRIPTS / "resegment_srt.py"
SEGMENT_CHECK = SCRIPTS / "resegment_check.py"
IS_WINDOWS = platform.system().lower() == "windows"

# pythonw.exe has no console; send progress and tracebacks to a file.
ensure_stdio(LOGS / "entry.log")

DEFAULTS = {
    "pyvideotrans_home": "vendor/pyvideotrans",
    "index_tts2_home": "vendor/index-tts2-cli",
    "src_dir": "",
    "out_dir": "",
    "work_dir": "_work",
    "backend": "index",
    "source_lang": "en",
    "target_lang": "zh-cn",
    "subtitle_mode": "hard",
    "translate_channel": "deepseek",
    "deepseek_model": "deepseek-flash",
    "asr_model": "large-v3-turbo",
    # 源视频旁有 .en-us.vtt / .en.vtt / .en.srt 时直接导入，跳过 ASR
    "sidecar_subs": True,
    "index_api": "http://127.0.0.1:8791",
    "index_url": "http://127.0.0.1:7861",
    "index_api_port": 8791,
    "shim_port": 7861,
    "index_fast": False,
    "index_diffusion_steps": 25,
    "index_restart_every": 0,
    "restart_before_tts": True,
    "pin_daemon": True,
    "probe": True,
    "daemon_affinity_mask": "0xFFFF",
    "gpu_lock_clock": 0,
    # 质量优先：逐句参考保留情感，同时用说话人聚类做合并键
    "clone_ref_mode": "line",
    "speaker_max": 0,
    "speaker_threshold": 0.72,
    "speaker_merge_sim": 0.80,
    "speaker_self_consistency": 0.55,
    "mt_workers": 4,
    # 声音优先：TTS 原速，不再为了槽位拉伸音频；画面片段按音频长度做 PTS 微调。
    "voice_autorate": False,
    "video_autorate": True,
    # 翻译前合并源碎句、翻译后按句子边界重切，避免完整句被字幕块切断。
    "resegment": True,
    "resegment_max_gap_ms": 900,
    "resegment_source_max_chars": 140,
    "resegment_target_max_chars": 44,
    # DeepSeek audits the rule-based sentence boundaries; it only proposes
    # TTS groups, while display cues stay at the rule-based boundaries.
    "resegment_llm_check": True,
    # 配音单元拟合：合并同说话人的相邻短句，再压缩仍超长的句子。
    # 预测模型按 IndexTTS2 实测拟合：audio ≈ 1.30s + 0.123s/汉字。
    "fit_merge": True,
    "fit_condense": True,
    # 仅 IndexTTS2 自动启用；其他后端标定后可用 fit_force 强制
    "fit_force": False,
    "fit_max_speed": 1.12,
    "fit_max_chars": 44,
    "fit_wrap_width": 22,
    "fit_gap_ms": 500,
    "fit_pred_a": 1.30,
    "fit_pred_b": 0.123,
    "fit_condense_speed": 1.25,
    "max_audio_speed_rate": 1.0,
    "max_audio_slow_rate": 1.0,
    "max_video_pts_rate": 1.5,
    "dubb_min_pause_ms": 200,
    # 自然短停顿；只有超过它的空档才放慢填缝，避免每条短句都被拉慢
    "dubb_natural_pause_ms": 600,
    # video_autorate 会让总时长随音频变化；QC 允许的时长漂移（秒）
    "duration_tol_s": 10.0,
    "loudness": -18.0,
    "final_suffix": "",
    "glossary": "assets/glossary.txt",
}


def resolve(value: str | Path, base: Path = SKILL) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        try:
            cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
        except Exception:
            pass
    for key in ("pyvideotrans_home", "index_tts2_home", "glossary"):
        if cfg.get(key):
            cfg[key] = str(resolve(cfg[key]))
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def pvt_python(cfg: dict) -> Path:
    p = Path(cfg["pyvideotrans_home"])
    return p / (".venv/Scripts/python.exe" if IS_WINDOWS else ".venv/bin/python")


def itt_python(cfg: dict) -> Path:
    venv = Path(cfg["index_tts2_home"]) / "venv"
    if IS_WINDOWS:
        # Prefer the *base* pythonw over venv\Scripts\pythonw.exe: the latter
        # is a uv launcher that starts the real python.exe as a child, and that
        # child creates its own conhost (a flashing black window).  The venv's
        # site-packages are added to PYTHONPATH in _index_env.
        pyvenv = venv / "pyvenv.cfg"
        if pyvenv.is_file():
            for line in pyvenv.read_text(encoding="utf-8", errors="replace").splitlines():
                key, _, value = line.partition("=")
                if key.strip().lower() == "home" and value.strip():
                    base = Path(value.strip()) / "pythonw.exe"
                    if base.is_file():
                        return base
                    break
        for rel in ("pythonw.exe", "Scripts/pythonw.exe",
                    "python.exe", "Scripts/python.exe"):
            if (venv / rel).exists():
                return venv / rel
        return venv / "Scripts/python.exe"
    return venv / "bin/python"


def itt_cli(cfg: dict) -> Path:
    p = Path(cfg["index_tts2_home"]) / "scripts"
    return p / ("indextts2.cmd" if IS_WINDOWS else "indextts2.sh")


def env(cfg: dict) -> dict:
    e = os.environ.copy()
    paths = []
    ff = TOOLS / "ffmpeg" / "bin"
    if ff.exists():
        paths.append(str(ff))
    rb = TOOLS / "rubberband" / "rubberband-4.0.0-gpl-executable-windows"
    if rb.exists():
        paths.append(str(rb))
    if (TOOLS / "uv").exists():
        paths.append(str(TOOLS / "uv"))
    e["PATH"] = os.pathsep.join(paths + [e.get("PATH", "")])
    e["PYVIDEOTRANS_HOME"] = cfg["pyvideotrans_home"]
    e["INDEXTTS2_HOME"] = cfg["index_tts2_home"]
    e["INDEXTTS2_API"] = cfg["index_api"]
    e["INDEXTTS2_FAST"] = "1" if cfg.get("index_fast") else "0"
    e["INDEXTTS2_CLI"] = str(itt_cli(cfg))
    e["INDEXTTS2_RESTART_EVERY"] = str(cfg.get("index_restart_every", 0))
    e["VTV_RESTART_BEFORE_TTS"] = "1" if cfg.get("restart_before_tts", True) else "0"
    e["VTV_PIN_DAEMON"] = "1" if cfg.get("pin_daemon", True) else "0"
    e["VTV_MAX_AUDIO_SPEED_RATE"] = str(cfg.get("max_audio_speed_rate", 30.0))
    e["VTV_MAX_AUDIO_SLOW_RATE"] = str(cfg.get("max_audio_slow_rate", 1.05))
    e["VTV_MAX_VIDEO_PTS_RATE"] = str(cfg.get("max_video_pts_rate", 1.5))
    e["VTV_DUBB_MIN_PAUSE_MS"] = str(cfg.get("dubb_min_pause_ms", 200))
    e["VTV_DUBB_NATURAL_PAUSE_MS"] = str(cfg.get("dubb_natural_pause_ms", 600))
    e["VTV_FIT_MERGE"] = "1" if cfg.get("fit_merge", True) else "0"
    e["VTV_FIT_CONDENSE"] = "1" if cfg.get("fit_condense", True) else "0"
    e["VTV_FIT_FORCE"] = "1" if cfg.get("fit_force", False) else "0"
    e["VTV_FIT_MAX_SPEED"] = str(cfg.get("fit_max_speed", 1.12))
    e["VTV_FIT_MAX_CHARS"] = str(cfg.get("fit_max_chars", 44))
    e["VTV_FIT_WRAP_WIDTH"] = str(cfg.get("fit_wrap_width", 22))
    e["VTV_FIT_GAP_MS"] = str(cfg.get("fit_gap_ms", 500))
    e["VTV_FIT_PRED_A"] = str(cfg.get("fit_pred_a", 1.30))
    e["VTV_FIT_PRED_B"] = str(cfg.get("fit_pred_b", 0.123))
    e["VTV_FIT_CONDENSE_SPEED"] = str(cfg.get("fit_condense_speed", 1.25))
    e["VTV_GLOSSARY_FILE"] = str(cfg.get("glossary", ""))
    e["VTV_CLONE_REF_MODE"] = str(cfg.get("clone_ref_mode", "cluster") or "cluster")
    e["VTV_SPK_MAX"] = str(cfg.get("speaker_max", 0) or 0)
    e["VTV_SPK_THRESHOLD"] = str(cfg.get("speaker_threshold", 0.72) or 0.72)
    e["VTV_SPK_MERGE_SIM"] = str(cfg.get("speaker_merge_sim", 0.80) or 0.80)
    e["VTV_SPK_SELF_CONSISTENCY"] = str(
        cfg.get("speaker_self_consistency", 0.55) or 0.55)
    try:
        e["VTV_ITT_PYTHON"] = str(itt_python(cfg))
        # speaker_cluster.py runs on the base pythonw, so it needs the venv's
        # site-packages explicitly (the base interpreter has none of torch).
        e["VTV_ITT_SITE"] = str(
            Path(cfg["index_tts2_home"]) / "venv" / "Lib" / "site-packages")
    except Exception:
        pass
    e.setdefault("PYTHONUTF8", "1")
    e.setdefault("PYTHONIOENCODING", "utf-8")
    if cfg.get("video_autorate"):
        e["VTV_DURATION_TOL_S"] = str(cfg.get("duration_tol_s", 10.0))
    if cfg.get("_tts_srt"):
        # TTS units are already complete sentences from the DeepSeek audit;
        # don't merge/condense them again (subtitles stay rule-based).
        e["VTV_TTS_SRT"] = str(cfg["_tts_srt"])
        e["VTV_FIT_MERGE"] = "0"
        e["VTV_FIT_CONDENSE"] = "0"
    if IS_WINDOWS:
        # Covers Python grandchildren we do not launch ourselves (the IndexTTS2
        # API's ffmpeg calls, uv -> cli.py).  See _winhide_site/sitecustomize.py.
        site = SCRIPTS / "_winhide_site"
        if site.is_dir():
            prev = e.get("PYTHONPATH", "")
            e["PYTHONPATH"] = str(site) + (os.pathsep + prev if prev else "")
    return e


def run(cmd: list[str], cfg: dict, log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8", errors="replace") as f:
        f.write(f"\n$ {' '.join(cmd)}\n")
        f.flush()
        return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, env=env(cfg)).returncode


def healthy(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def wait_healthy(url: str, seconds: int = 60) -> bool:
    for _ in range(seconds):
        if healthy(url):
            return True
        time.sleep(1)
    return False


# --------------------------------------------------------------------------
# setup / doctor / serve
# --------------------------------------------------------------------------
def cmd_setup(args, cfg: dict) -> int:
    pvt = resolve(args.reuse_pvt or cfg["pyvideotrans_home"])
    itt = resolve(args.reuse_itt or cfg["index_tts2_home"])
    cfg["pyvideotrans_home"] = str(pvt)
    cfg["index_tts2_home"] = str(itt)
    for d in (LOGS, STATE, TOOLS):
        d.mkdir(parents=True, exist_ok=True)

    print(f"[setup] pyVideoTrans home: {pvt}")
    if pvt == PVT_DEFAULT.resolve():
        cmd = [sys.executable, str(WRAPPER), "setup", "--models", cfg["asr_model"]]
        if args.proxy:
            cmd += ["--proxy", args.proxy]
        rc = run(cmd, cfg, LOGS / "setup_pyvideotrans.log")
        if rc != 0:
            print(f"[setup] pyVideoTrans setup failed (rc={rc}); see .runtime/logs/setup_pyvideotrans.log")
            return rc
    else:
        print("[setup] external pyVideoTrans: skip dependency install")

    print(f"[setup] IndexTTS2 home: {itt}")
    if itt == ITT_DEFAULT.resolve():
        rc = run([sys.executable, str(itt / "scripts" / "indextts2.py"), "setup"],
                 cfg, LOGS / "setup_indextts2.log")
        if rc != 0:
            print(f"[setup] IndexTTS2 setup failed (rc={rc}); see .runtime/logs/setup_indextts2.log")
            return rc
    else:
        print("[setup] external IndexTTS2: skip dependency install")

    # Rubberband is optional but strongly recommended for audio time-stretch.
    if not shutil.which("rubberband", path=env(cfg)["PATH"]):
        print("[setup] WARNING: rubberband not found. Windows bundle should include "
              "tools/rubberband; Linux: apt install rubberband-cli; macOS: brew install rubberband")

    # Configure pyVideoTrans credentials/channels.
    params_path = pvt / "videotrans" / "params.json"
    params = {}
    if params_path.exists():
        try:
            params = json.loads(params_path.read_text(encoding="utf-8"))
        except Exception:
            params = {}
    key = args.deepseek_key or os.environ.get("DEEPSEEK_API_KEY", "")
    params.update({
        "translate_type": 5,
        "deepseek_key": key or params.get("deepseek_key", ""),
        "deepseek_model": cfg["deepseek_model"],
        "tts_type": 12 if cfg["backend"] == "index" else 1,
        "index_url": cfg["index_url"],
        "indextts_version": 0,
        "model_name": cfg["asr_model"],
        "stt_model_name": cfg["asr_model"],
    })
    params_path.parent.mkdir(parents=True, exist_ok=True)
    params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")

    cfg["deepseek_model"] = params.get("deepseek_model", cfg["deepseek_model"])
    save_config(cfg)
    print(f"[setup] wrote {CONFIG}")
    if not params.get("deepseek_key"):
        print("[setup] NOTE: no DeepSeek key. Set DEEPSEEK_API_KEY or pass --deepseek-key.")
    return cmd_doctor(args, cfg)


def cmd_doctor(_args, cfg: dict) -> int:
    pvt = Path(cfg["pyvideotrans_home"])
    itt = Path(cfg["index_tts2_home"])
    checks = []

    def add(name: str, ok: bool, detail: str = ""):
        checks.append((name, ok, detail))
        print(f"[{'OK ' if ok else 'FAIL'}] {name:28} {detail}")

    add("OS", True, f"{platform.system()} {platform.machine()}")
    add("uv", bool(shutil.which("uv") or (TOOLS / "uv").exists()),
        shutil.which("uv") or str(TOOLS / "uv"))
    add("ffmpeg", bool(shutil.which("ffmpeg") or (TOOLS / "ffmpeg" / "bin" / "ffmpeg.exe").exists()),
        shutil.which("ffmpeg") or str(TOOLS / "ffmpeg" / "bin"))
    add("rubberband", bool(shutil.which("rubberband", path=env(cfg)["PATH"])),
        "pitch-preserving time stretch")
    add("pyVideoTrans source", (pvt / "cli.py").exists(), str(pvt))
    add("pyVideoTrans venv", pvt_python(cfg).exists(), str(pvt_python(cfg)))
    add("IndexTTS2 source", (itt / "scripts" / "indextts2.py").exists(), str(itt))
    add("IndexTTS2 venv", itt_python(cfg).exists(), str(itt_python(cfg)))
    add("IndexTTS2 checkpoints", (itt / "checkpoints" / "gpt.pth").exists(),
        str(itt / "checkpoints"))
    params = pvt / "videotrans" / "params.json"
    key = ""
    if params.exists():
        try:
            key = json.loads(params.read_text(encoding="utf-8")).get("deepseek_key", "")
        except Exception:
            pass
    add("DeepSeek key", bool(key or os.environ.get("DEEPSEEK_API_KEY")),
        "env or params.json")
    add("Index API :8791", healthy(cfg["index_api"] + "/health"), cfg["index_api"])
    add("Gradio shim :7861", healthy(cfg["index_url"] + "/"), cfg["index_url"])
    add("glossary", Path(cfg["glossary"]).exists(), cfg["glossary"])

    critical = [ok for name, ok, _ in checks
                if name in ("pyVideoTrans source", "pyVideoTrans venv",
                            "IndexTTS2 source", "IndexTTS2 venv")]
    return 0 if all(critical) else 1


def _pid_file(name: str) -> Path:
    return STATE / f"{name}.pid"


def _read_pid(name: str) -> int | None:
    p = _pid_file(name)
    try:
        return int(p.read_text().strip())
    except Exception:
        return None


def _kill(name: str) -> None:
    pid = _read_pid(name)
    if not pid:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True)
        else:
            killpg = getattr(os, "killpg", None)
            (killpg(pid, 15) if killpg else os.kill(pid, 15))
    except Exception:
        pass
    _pid_file(name).unlink(missing_ok=True)


def _popen_kwargs() -> dict:
    if IS_WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        # The API and shim are long-lived; without this they each own a visible
        # console window when the skill runs from an agent/editor (no console).
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return {"creationflags": flags}
    return {"start_new_session": True}


def _index_env(cfg: dict) -> dict:
    """Env for the IndexTTS2 API/shim: torch thread count for the daemon."""
    e = env(cfg)
    if IS_WINDOWS:
        # The daemon is started from the base pythonw (see spawn_daemon), so
        # its venv site-packages must be supplied explicitly.
        site = Path(cfg["index_tts2_home"]) / "venv" / "Lib" / "site-packages"
        if site.is_dir():
            prev = e.get("PYTHONPATH", "")
            if str(site) not in prev.split(os.pathsep):
                e["PYTHONPATH"] = str(site) + (os.pathsep + prev if prev else "")
    threads = str(cfg.get("index_threads", 0) or "")
    if threads:
        # Only OpenMP: forcing MKL/NumExpr to the same count made small GEMMs
        # thrash (observed 4x slower GPT decode).
        e["OMP_NUM_THREADS"] = threads
    steps = str(cfg.get("index_diffusion_steps", 0) or "")
    if steps:
        e["INDEXTTS2_DIFFUSION_STEPS"] = steps
    if cfg.get("probe", True):
        e.setdefault("VTV_PROBE_FILE", str((LOGS / "tts_probe.jsonl").resolve()))
    e.setdefault("INDEXTTS2_PIN_SELF",
                 "1" if cfg.get("pin_daemon", True) else "0")
    e.setdefault("INDEXTTS2_HIGH_PRIORITY",
                 "1" if cfg.get("index_high_priority", True) else "0")
    e.setdefault("INDEXTTS2_AFFINITY_MASK",
                 str(cfg.get("daemon_affinity_mask", "0xFFFF")))
    return e


def _set_high_priority(pid: int) -> None:
    """Raise a process to High priority (Windows) / nice -5 (POSIX)."""
    if not IS_WINDOWS:
        try:
            os.setpriority(os.PRIO_PROCESS, int(pid), -5)
        except Exception:
            pass
        return
    try:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x0200 | 0x0400, False, int(pid))
        if handle:
            ctypes.windll.kernel32.SetPriorityClass(handle, 0x00000080)
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass


def _api_daemon_pid(cfg: dict) -> int | None:
    """The API's /health exposes the daemon pid once it is preloaded."""
    try:
        import urllib.request
        with urllib.request.urlopen(cfg["index_api"] + "/health", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data.get("daemon") or {}).get("pid")
    except Exception:
        return None


def _daemon_state(cfg: dict) -> str | None:
    try:
        import urllib.request
        with urllib.request.urlopen(cfg["index_api"] + "/health", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data.get("daemon") or {}).get("state")
    except Exception:
        return None


def _wait_daemon_ready(cfg: dict, seconds: int = 180) -> bool:
    for _ in range(max(1, seconds)):
        state = _daemon_state(cfg)
        if state == "ready":
            return True
        if state == "failed":
            return False
        time.sleep(1)
    return False


def _nvidia_smi(*args: str) -> int:
    exe = shutil.which("nvidia-smi")
    if not exe and IS_WINDOWS:
        exe = r"C:\Windows\System32\nvidia-smi.exe"
    if not exe:
        print("[gpu] nvidia-smi not found")
        return 127
    try:
        cp = subprocess.run([exe, *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
        if cp.returncode != 0:
            msg = (cp.stderr or cp.stdout or "").strip()[:300]
            print(f"[gpu] nvidia-smi {' '.join(args)} failed: {msg}")
        return cp.returncode
    except Exception as exc:  # noqa: BLE001
        print(f"[gpu] nvidia-smi failed: {exc}")
        return 1


def _apply_gpu_clock(cfg: dict) -> None:
    clock = int(cfg.get("gpu_lock_clock", 0) or 0)
    if clock <= 0:
        return
    if _nvidia_smi("-lgc", f"{clock},{clock}") == 0:
        print(f"[gpu] locked SM clock to {clock} MHz")


def _reset_gpu_clock(cfg: dict) -> None:
    if int(cfg.get("gpu_lock_clock", 0) or 0) > 0:
        _nvidia_smi("-rgc")


def _ac_power() -> bool | None:
    """True=AC, False=battery, None=unknown/not Windows."""
    if not IS_WINDOWS:
        return None
    try:
        import ctypes

        class PowerStatus(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_byte),
                ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte),
                ("Reserved1", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        status = PowerStatus()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None
        if status.ACLineStatus == 1:
            return True
        if status.ACLineStatus == 0:
            return False
    except Exception:
        pass
    return None


def _check_power(args) -> bool:
    if getattr(args, "allow_battery", False):
        return True
    ac = _ac_power()
    if ac is False:
        print("[power] battery power detected: CPU/GPU are throttled and TTS can be "
              "2-3x slower. Connect AC, or pass --allow-battery to proceed.")
        return False
    return True


def cmd_serve(args, cfg: dict) -> int:
    STATE.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    if args.stop:
        _kill("shim")
        _kill("api")
        _reset_gpu_clock(cfg)
        print("[serve] stopped")
        return 0
    api_ok = healthy(cfg["index_api"] + "/health")
    shim_ok = healthy(cfg["index_url"] + "/")
    if api_ok and shim_ok:
        print("[serve] already running")
        # The daemon may have exited on its idle timeout; the first /v1/tts
        # request will start it again.  Do not block serve for 3 minutes.
        ready = _wait_daemon_ready(cfg, 5)
        _apply_gpu_clock(cfg)
        print(f"[serve] daemon_ready={ready}")
        return 0
    py = itt_python(cfg)
    if not py.exists():
        print(f"[serve] IndexTTS2 venv missing: {py}; run setup")
        return 2

    if not api_ok:
        api_log = (LOGS / "index_api.log").open("a", encoding="utf-8", errors="replace")
        api = subprocess.Popen(
            [str(py), str(Path(cfg["index_tts2_home"]) / "scripts" / "indextts2.py"),
             "api", "--listen-port", str(cfg["index_api_port"]), "--json"],
            stdout=api_log, stderr=subprocess.STDOUT, env=_index_env(cfg), **_popen_kwargs())
        _pid_file("api").write_text(str(api.pid))
        if cfg.get("index_high_priority", True):
            _set_high_priority(api.pid)
        print(f"[serve] IndexTTS2 API pid={api.pid} -> {cfg['index_api']}")
    else:
        print("[serve] API already running")

    if not shim_ok:
        shenv = _index_env(cfg)
        shenv["INDEXTTS2_API"] = cfg["index_api"]
        shenv["INDEXTTS2_FAST"] = "1" if cfg.get("index_fast") else "0"
        shenv["INDEXTTS2_RESTART_EVERY"] = str(cfg.get("index_restart_every", 25))
        shenv["INDEXTTS2_CLI"] = str(itt_cli(cfg))
        shenv["SHIM_PORT"] = str(cfg["shim_port"])
        shim_log = (LOGS / "gradio_shim.log").open("a", encoding="utf-8", errors="replace")
        shim = subprocess.Popen([str(py), str(SHIM)], stdout=shim_log,
                                stderr=subprocess.STDOUT, env=shenv, **_popen_kwargs())
        _pid_file("shim").write_text(str(shim.pid))
        if cfg.get("index_high_priority", True):
            _set_high_priority(shim.pid)
        print(f"[serve] Gradio shim pid={shim.pid} -> {cfg['index_url']}")
    else:
        print("[serve] shim already running")

    ok_api = api_ok or wait_healthy(cfg["index_api"] + "/health", 60)
    ok_shim = shim_ok or wait_healthy(cfg["index_url"] + "/", 60)
    # A freshly started API preloads the daemon; an already-running API may
    # have let it idle out, in which case the next request starts it again.
    ready = _wait_daemon_ready(cfg, 180 if not api_ok else 5) if ok_api else False
    if cfg.get("index_high_priority", True):
        daemon_pid = _api_daemon_pid(cfg)
        if daemon_pid:
            _set_high_priority(daemon_pid)
    _apply_gpu_clock(cfg)
    print(f"[serve] api={ok_api} shim={ok_shim} daemon_ready={ready}")
    return 0 if ok_api and ok_shim and (ready or api_ok) else 1


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------
def videos(cfg: dict, arg: str) -> list[tuple[str, Path]]:
    src = Path(cfg["src_dir"])
    if not src.is_dir():
        raise SystemExit(f"--src not found: {src}")
    all_v = sorted(src.glob("*.mp4"))
    if arg.strip().lower() in ("all", "*", ""):
        return [(p.stem, p) for p in all_v]
    wanted = {x.strip() for x in arg.split(",") if x.strip()}
    out = [(p.stem, p) for p in all_v
           if p.stem in wanted or any(p.stem.startswith(w) for w in wanted)]
    if not out:
        raise SystemExit(f"no videos matched {arg!r}")
    return out


def paths(cfg: dict, stem: str) -> dict:
    out = Path(cfg["out_dir"])
    work = resolve(cfg["work_dir"], out)
    return {
        "prep": work / "prep" / stem,
        "dub": work / stem,
        "final": out / f"{stem}{cfg.get('final_suffix', '')}.mp4",
        "subs": out / "_字幕",
        "log": LOGS / f"{stem}.log",
    }


def state_path(stem: str) -> Path:
    return STATE / f"{stem}.json"


def load_state(stem: str) -> dict:
    p = state_path(stem)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(stem: str, **kw) -> None:
    d = load_state(stem)
    d.update(kw)
    d["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state_path(stem).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def wrapper_cmd(cfg: dict, *args: str) -> list[str]:
    return [sys.executable, str(WRAPPER), *args, "--home", cfg["pyvideotrans_home"]]


SIDECAR_SUFFIXES = (".en-us.vtt", ".en.vtt", ".en.srt", ".vtt", ".srt")


def find_sidecar(src: Path) -> Path | None:
    """Find an existing source-language subtitle next to the video."""
    base = src.with_suffix("")
    for suffix in SIDECAR_SUFFIXES:
        candidate = Path(str(base) + suffix)
        if candidate.is_file():
            return candidate
    return None


def import_sidecar(side: Path, dest: Path, cfg: dict) -> bool:
    """Convert a sidecar VTT/SRT to a clean UTF-8 SRT without ASR."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if side.suffix.lower() == ".srt":
        raw = side.read_text(encoding="utf-8-sig", errors="replace")
    else:
        tmp = dest.with_name(dest.stem + ".sidecar.tmp.srt")
        cp = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(side),
             "-c:s", "srt", str(tmp)],
            env=env(cfg), capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        if cp.returncode != 0 or not tmp.exists():
            print(f"[prep] sidecar conversion failed: {(cp.stderr or '').strip()[:200]}")
            tmp.unlink(missing_ok=True)
            return False
        raw = tmp.read_text(encoding="utf-8-sig", errors="replace")
        tmp.unlink(missing_ok=True)
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if "-->" not in text:
        return False
    dest.write_text(text + "\n", encoding="utf-8")
    return True


def resegment_file(cfg: dict, inp: Path, out: Path, max_chars: int,
                   lang: str, log: Path) -> bool:
    if not cfg.get("resegment", True):
        return False
    cmd = [sys.executable, str(SEGMENT), "--input", str(inp),
           "--output", str(out), "--max-chars", str(max(8, max_chars)),
           "--max-gap", str(max(0, int(cfg.get("resegment_max_gap_ms", 900)))),
           "--source-lang", str(lang)]
    return run(cmd, cfg, log) == 0


def resegment_source(cfg: dict, stem: str, p: dict, fresh: bool = False) -> None:
    """Merge ASR/VTT fragments before translation (original kept as en.orig.srt)."""
    en = p["prep"] / "en.srt"
    orig = p["prep"] / "en.orig.srt"
    if not en.exists() or not cfg.get("resegment", True):
        return
    if fresh:
        shutil.copy2(en, orig)
    elif orig.exists():
        return
    else:
        shutil.copy2(en, orig)
    tmp = p["prep"] / "en.reseg.tmp.srt"
    if resegment_file(cfg, orig, tmp, int(cfg.get("resegment_source_max_chars", 140)),
                      cfg["source_lang"], p["log"]):
        tmp.replace(en)
        print(f"[prep] {stem}: resegment source -> {en}")
    else:
        tmp.unlink(missing_ok=True)
        print(f"[prep] {stem}: source resegment failed; keeping original cues")


def resegment_target(cfg: dict, stem: str, p: dict, target: Path) -> None:
    """Split/merge translated cues at sentence boundaries.

    The rule pass fixes display cues.  DeepSeek then audits the whole transcript
    and writes merged *TTS units* (possibly longer than one display cue); the
    dubbing stage uses those while the burned/archived subtitles keep the
    display cue boundaries.
    """
    if not cfg.get("resegment", True) or not target.exists():
        return
    max_chars = int(cfg.get("resegment_target_max_chars", 44))
    tmp = p["prep"] / f"{target.stem}.reseg.tmp.srt"
    if resegment_file(cfg, target, tmp, max_chars, cfg["target_lang"], p["log"]):
        tmp.replace(target)
        print(f"[prep] {stem}: resegment target -> {target}")
    else:
        tmp.unlink(missing_ok=True)

    if not cfg.get("resegment_llm_check", True):
        return
    checked = p["prep"] / f"{target.stem}.llm.tmp.srt"
    tts_tmp = p["prep"] / f"{target.stem}.tts.tmp.srt"
    cmd = [sys.executable, str(SEGMENT_CHECK), "--input", str(target),
           "--output", str(checked), "--tts-output", str(tts_tmp),
           "--max-chars", str(max_chars), "--lang", cfg["target_lang"]]
    rc = run(cmd, cfg, p["log"])
    if rc == 0 and checked.exists():
        checked.replace(target)
    else:
        checked.unlink(missing_ok=True)
    if tts_tmp.exists():
        tts_tmp.replace(p["prep"] / f"{target.stem}.tts.srt")
        print(f"[prep] {stem}: DeepSeek TTS groups -> {target.stem}.tts.srt")


def asr_one(cfg: dict, stem: str, src: Path, force: bool = False) -> bool:
    p = paths(cfg, stem)
    en = p["prep"] / "en.srt"
    if en.exists() and not force:
        resegment_source(cfg, stem, p)
        print(f"[prep] {stem}: ASR cached")
        return True
    side = find_sidecar(src) if cfg.get("sidecar_subs", True) else None
    if side:
        if import_sidecar(side, en, cfg):
            save_state(stem, asr=True, asr_source="sidecar")
            resegment_source(cfg, stem, p, fresh=True)
            print(f"[prep] {stem}: sidecar {side.name} -> {en}")
            return True
        print(f"[prep] {stem}: sidecar import failed; falling back to ASR")
    p["prep"].mkdir(parents=True, exist_ok=True)
    stt_dir = p["prep"] / "stt"
    shutil.rmtree(stt_dir, ignore_errors=True)
    cmd = wrapper_cmd(cfg, "stt", str(src), "--source", cfg["source_lang"],
                      "--asr-model", cfg["asr_model"], "--outdir", str(stt_dir),
                      "--overwrite")
    rc = run(cmd, cfg, p["log"])
    produced = stt_dir / f"{stem}.srt"
    if rc != 0 or not produced.exists():
        print(f"[prep] {stem}: ASR failed rc={rc}")
        return False
    shutil.copy2(produced, en)
    resegment_source(cfg, stem, p, fresh=True)
    save_state(stem, asr=True)
    print(f"[prep] {stem}: ASR -> {en}")
    return True


def mt_one(cfg: dict, stem: str, force: bool = False) -> bool:
    p = paths(cfg, stem)
    en = p["prep"] / "en.srt"
    target = p["prep"] / f"{cfg['target_lang']}.srt"
    if not en.exists():
        print(f"[prep] {stem}: no en.srt")
        return False
    if target.exists() and not force:
        print(f"[prep] {stem}: translation cached")
        return True
    mt_dir = p["prep"] / "mt"
    shutil.rmtree(mt_dir, ignore_errors=True)
    cmd = wrapper_cmd(cfg, "sts", str(en), "--source", cfg["source_lang"],
                      "--target", cfg["target_lang"], "--channel", cfg["translate_channel"],
                      "--outdir", str(mt_dir), "--overwrite")
    rc = run(cmd, cfg, p["log"])
    produced = mt_dir / f"en.{cfg['target_lang']}.srt"
    if not produced.exists():
        alternatives = list(mt_dir.glob(f"*.{cfg['target_lang']}.srt"))
        produced = alternatives[0] if alternatives else produced
    if rc != 0 or not produced.exists():
        print(f"[prep] {stem}: translation failed rc={rc}")
        return False
    shutil.copy2(produced, target)
    resegment_target(cfg, stem, p, target)
    save_state(stem, mt=True)
    print(f"[prep] {stem}: MT -> {target}")
    return True


def prep(cfg: dict, items: list[tuple[str, Path]], force: bool = False) -> bool:
    ok = True
    for stem, src in items:
        ok = asr_one(cfg, stem, src, force) and ok
    workers = max(1, min(int(cfg.get("mt_workers", 4)), len(items)))
    if workers > 1:
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda it: mt_one(cfg, it[0], force), items))
        ok = ok and all(results)
    else:
        for stem, _ in items:
            ok = mt_one(cfg, stem, force) and ok
    return ok


def dub_one(cfg: dict, stem: str, src: Path, force: bool = False) -> bool:
    p = paths(cfg, stem)
    en = p["prep"] / "en.srt"
    target = p["prep"] / f"{cfg['target_lang']}.srt"
    if not en.exists() or not target.exists():
        print(f"[dub] {stem}: missing prep SRT")
        return False
    p["dub"].mkdir(parents=True, exist_ok=True)
    shutil.copy2(en, p["dub"] / f"{cfg['source_lang']}.srt")
    shutil.copy2(target, p["dub"] / f"{cfg['target_lang']}.srt")
    run_cfg = cfg
    tts_srt = p["prep"] / f"{target.stem}.tts.srt"
    if tts_srt.exists():
        try:
            n_tts = sum(1 for x in tts_srt.read_text(encoding="utf-8-sig",
                                                   errors="replace").splitlines()
                        if "-->" in x)
            n_disp = sum(1 for x in target.read_text(encoding="utf-8-sig",
                                                    errors="replace").splitlines()
                         if "-->" in x)
        except Exception:
            n_tts = n_disp = 0
        if 0 < n_tts < n_disp:
            dub_tts = p["dub"] / f"{cfg['target_lang']}.tts.srt"
            shutil.copy2(tts_srt, dub_tts)
            run_cfg = dict(cfg)
            run_cfg["_tts_srt"] = str(dub_tts)
            print(f"[dub] {stem}: TTS units {n_tts} (display cues {n_disp})")
    subtitle_type = {"none": 0, "hard": 1, "soft": 2, "dual-hard": 3,
                     "dual-soft": 4}.get(cfg.get("subtitle_mode", "hard"), 1)
    extra = []
    if cfg.get("voice_autorate", True):
        extra.append("--voice_autorate")
    if cfg.get("video_autorate", True):
        extra.append("--video_autorate")
    cmd = wrapper_cmd(
        cfg, "vtv", str(src),
        "--source", cfg["source_lang"], "--target", cfg["target_lang"],
        "--channel", cfg["translate_channel"],
        "--tts-channel", cfg["backend"], "--voice", "clone",
        "--subtitle-type", str(subtitle_type),
        "--outdir", str(p["dub"]), "--overwrite", "--no-clear-cache")
    if extra:
        cmd.append("--extra-args=" + " ".join(extra))
    rc = run(cmd, run_cfg, p["log"])
    produced = p["dub"] / f"{stem}.mp4"
    if rc != 0 or not produced.exists():
        print(f"[dub] {stem}: failed rc={rc}")
        return False
    save_state(stem, dub=True)
    print(f"[dub] {stem}: -> {produced}")
    return True


def media_duration(path: Path, cfg: dict) -> float:
    try:
        cp = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", str(path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env(cfg))
        return float((cp.stdout or "").strip())
    except Exception:
        return 0.0


def measure_loudnorm(path: Path, cfg: dict, target: str) -> dict | None:
    """First loudnorm pass: measure input loudness for a linear second pass."""
    try:
        cp = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(path), "-vn",
             "-af", f"loudnorm={target}:print_format=json",
             "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env(cfg))
        text = (cp.stderr or "") + (cp.stdout or "")
        m = re.search(r"\{\s*\"input_i\".*?\}", text, re.S)
        if not m:
            return None
        return json.loads(m.group(0))
    except Exception:
        return None


def post_one(cfg: dict, stem: str) -> bool:
    p = paths(cfg, stem)
    src_mp4 = p["dub"] / f"{stem}.mp4"
    if not src_mp4.exists():
        print(f"[post] {stem}: no work mp4")
        return False
    p["final"].parent.mkdir(parents=True, exist_ok=True)
    tmp = p["final"].with_suffix(".tmp.mp4")
    source = Path(cfg["src_dir"]) / f"{stem}.mp4"
    source_dur = media_duration(source, cfg) if source.exists() else 0.0
    dub_dur = media_duration(src_mp4, cfg)
    target = f"I={cfg.get('loudness', -18)}:TP=-1.5:LRA=11"
    measured = measure_loudnorm(src_mp4, cfg, target)
    af = f"loudnorm={target}"
    if measured and measured.get("input_i") not in (None, "-inf", "inf"):
        af = (f"loudnorm={target}"
              f":measured_I={measured.get('input_i')}"
              f":measured_TP={measured.get('input_tp')}"
              f":measured_LRA={measured.get('input_lra')}"
              f":measured_thresh={measured.get('input_thresh')}"
              f":offset={measured.get('target_offset')}:linear=true")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src_mp4),
           "-c:v", "copy", "-af", af,
           "-ar", "48000", "-c:a", "aac", "-b:a", "192k"]
    # Normal mode trims audio overrun at the source duration.  With
    # video_autorate, the dubbed video may legitimately be longer/shorter;
    # trim to its own duration instead so the tail is not cut off.
    trim = dub_dur if cfg.get("video_autorate") and dub_dur > 0 else source_dur
    if trim > 0:
        cmd += ["-t", f"{trim:.3f}"]
    cmd.append(str(tmp))
    rc = run(cmd, cfg, p["log"])
    if rc != 0 or not tmp.exists():
        print(f"[post] {stem}: loudnorm failed rc={rc}")
        return False
    tmp.replace(p["final"])
    p["subs"].mkdir(parents=True, exist_ok=True)
    target_srt = p["subs"] / f"{stem}.{cfg['target_lang']}.srt"
    source_srt = p["subs"] / f"{stem}.{cfg['source_lang']}.srt"
    for name, dest in ((f"{cfg['target_lang']}.srt", target_srt),
                       (f"{cfg['source_lang']}.srt", source_srt)):
        s = p["dub"] / name
        if s.exists():
            shutil.copy2(s, dest)
    rc = run([sys.executable, str(QC), "--video", str(p["final"]),
              "--source", str(Path(cfg["src_dir"]) / f"{stem}.mp4"),
              "--zh", str(target_srt), "--en", str(source_srt),
              "--glossary", cfg["glossary"],
              "--json-out", str(LOGS / f"{stem}.qc.json")],
             cfg, p["log"])
    save_state(stem, post=True, qc_ok=(rc == 0))
    print(f"[post] {stem}: -> {p['final']} (qc_ok={rc == 0})")
    return rc == 0


def cmd_status(args, cfg: dict) -> int:
    for stem, _ in videos(cfg, args.videos):
        st = load_state(stem)
        final = Path(cfg["out_dir"]) / f"{stem}{cfg.get('final_suffix', '')}.mp4"
        print(f"{stem[:60]:60} asr={int(bool(st.get('asr')))} "
              f"mt={int(bool(st.get('mt')))} dub={int(bool(st.get('dub')))} "
              f"final={int(final.exists())} qc={st.get('qc_ok')}")
    return 0


def ensure_services(cfg: dict) -> None:
    if cfg.get("backend") != "index":
        return
    api_ok = healthy(cfg["index_api"] + "/health")
    shim_ok = healthy(cfg["index_url"] + "/")
    if not (api_ok and shim_ok):
        print("[dub] IndexTTS2 API/shim not fully healthy; starting missing service")
        cmd_serve(argparse.Namespace(stop=False), cfg)


def cmd_run(args, cfg: dict) -> int:
    if not _check_power(args):
        return 2
    items = videos(cfg, args.videos)
    ok = prep(cfg, items, args.force)
    ensure_services(cfg)
    for stem, src in items:
        ok = dub_one(cfg, stem, src, args.force) and ok
    for stem, _ in items:
        ok = post_one(cfg, stem) and ok
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Portable video translation pipeline")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="install dependencies/models and write config")
    p.add_argument("--reuse-pvt", help="use an existing pyVideoTrans checkout")
    p.add_argument("--reuse-itt", help="use an existing IndexTTS2 checkout")
    p.add_argument("--deepseek-key", default="")
    p.add_argument("--proxy", default="")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("doctor", help="check dependencies")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("serve", help="start IndexTTS2 API + Gradio shim")
    p.add_argument("--stop", action="store_true")
    p.set_defaults(func=cmd_serve)

    for name in ("prep", "dub", "post", "run", "status", "polish", "qc", "report"):
        p = sub.add_parser(name)
        p.add_argument("--src", default="", help="source directory of mp4 files")
        p.add_argument("--out", default="", help="output directory")
        p.add_argument("--work", dest="work_dir", default="", help="work directory (default: <out>/_work)")
        p.add_argument("--videos", default="all")
        p.add_argument("--source", dest="source_lang", default="")
        p.add_argument("--target", dest="target_lang", default="")
        p.add_argument("--backend", default="", choices=["", "index", "qwen3local", "f5"])
        p.add_argument("--subtitle", dest="subtitle_mode", default="",
                       choices=["", "none", "hard", "soft", "dual-hard", "dual-soft"])
        p.add_argument("--force", action="store_true")
        p.add_argument("--mt-workers", type=int, default=0)
        p.add_argument("--video", default="")
        p.add_argument("--source-video", default="")
        p.add_argument("--zh", default="")
        p.add_argument("--en", default="")
        p.add_argument("--glossary", default="")
        p.add_argument("--logs", default="")
        p.add_argument("--json-out", default="")
        p.add_argument("--allow-battery", action="store_true")
        p.set_defaults(func=None)

    args = ap.parse_args()
    # `--src` / `--out` are stored as `src` / `out` by argparse, but the config
    # merge below reads `src_dir` / `out_dir`.  Normalise them first so the
    # CLI overrides actually take effect.
    if getattr(args, "src", ""):
        args.src_dir = args.src
    if getattr(args, "out", ""):
        args.out_dir = args.out
    cfg = load_config()
    for key in ("src_dir", "out_dir", "work_dir", "source_lang", "target_lang",
                "backend", "subtitle_mode", "mt_workers", "glossary"):
        val = getattr(args, key, None)
        if val not in (None, "", 0):
            cfg[key] = val
    if args.command in ("prep", "dub", "post", "run", "status") and not cfg.get("src_dir"):
        ap.error("--src is required (or set src_dir in config.json)")
    if args.command in ("prep", "dub", "post", "run", "status") and not cfg.get("out_dir"):
        ap.error("--out is required (or set out_dir in config.json)")
    if cfg.get("src_dir"):
        cfg["src_dir"] = str(resolve(cfg["src_dir"]))
    if cfg.get("out_dir"):
        cfg["out_dir"] = str(resolve(cfg["out_dir"]))
    if cfg.get("glossary"):
        cfg["glossary"] = str(resolve(cfg["glossary"]))

    if args.command == "setup":
        return cmd_setup(args, cfg)
    if args.command == "doctor":
        return cmd_doctor(args, cfg)
    if args.command == "serve":
        return cmd_serve(args, cfg)
    if args.command in ("prep", "dub", "post", "run", "status"):
        if args.command == "status":
            return cmd_status(args, cfg)
        if args.command == "prep":
            return 0 if prep(cfg, videos(cfg, args.videos), args.force) else 1
        if args.command == "dub":
            if not _check_power(args):
                return 2
            ensure_services(cfg)
            ok = True
            for stem, src in videos(cfg, args.videos):
                ok = dub_one(cfg, stem, src, args.force) and ok
            return 0 if ok else 1
        if args.command == "post":
            ok = True
            for stem, _ in videos(cfg, args.videos):
                ok = post_one(cfg, stem) and ok
            return 0 if ok else 1
        return cmd_run(args, cfg)
    if args.command == "qc":
        cmd = [sys.executable, str(QC), "--video", args.video, "--zh", args.zh,
               "--en", args.en, "--source", args.source_video or args.video,
               "--glossary", args.glossary or cfg["glossary"]]
        if args.json_out:
            cmd += ["--json-out", args.json_out]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env(cfg))
        if p.stdout:
            print(p.stdout)
        if p.stderr:
            print(p.stderr, file=sys.stderr)
        return p.returncode
    if args.command == "polish":
        return run([sys.executable, str(POLISH), "--srt", args.zh or args.video,
                    "--glossary", args.glossary or cfg["glossary"],
                    "--out", args.json_out or ((args.zh or args.video) + ".polished.srt")],
                   cfg, LOGS / "polish.log")
    if args.command == "report":
        logs = args.logs or str(LOGS)
        return run([sys.executable, str(REPORT), "--logs", logs,
                    "--out", str(Path(cfg["out_dir"]) / "QC_SUMMARY.md")],
                   cfg, LOGS / "report.log")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
