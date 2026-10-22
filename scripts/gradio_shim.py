# -*- coding: utf-8 -*-
"""Minimal Gradio /gen_single bridge from pyVideoTrans index channel to IndexTTS2.

pyVideoTrans's `index` TTS channel calls a Gradio app endpoint named
`/gen_single` with `prompt` (reference audio file) and `text`. This shim
forwards those calls to the IndexTTS2 HTTP API (POST /v1/tts) and returns
the generated wav path.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _winhide import hide_console_windows  # noqa: E402

hide_console_windows()

import gradio as gr  # noqa: E402
import requests  # noqa: E402

API = os.environ.get("INDEXTTS2_API", "http://127.0.0.1:8791").rstrip("/")
KEY = os.environ.get("INDEXTTS2_API_KEY", "")
FAST = os.environ.get("INDEXTTS2_FAST", "1") not in ("0", "false", "False", "")
# IndexTTS2 slows down after ~25 jobs on this laptop; restart it periodically.
RESTART_EVERY = int(os.environ.get("INDEXTTS2_RESTART_EVERY", "25") or 0)
ITT_CLI = os.environ.get("INDEXTTS2_CLI", "")
if not ITT_CLI:
    _home = os.environ.get("INDEXTTS2_HOME", "")
    if _home:
        _scripts = os.path.join(_home, "scripts")
        ITT_CLI = os.path.join(_scripts, "indextts2.cmd" if os.name == "nt" else "indextts2.sh")

_counter = 0

_SKILL_DIR = Path(__file__).resolve().parent.parent
REF_CACHE = Path(os.environ.get(
    "INDEXTTS2_REF_CACHE", str(_SKILL_DIR / ".runtime" / "ref_cache")))


def _stable_ref(prompt: str) -> str:
    """Give IndexTTS2 a path that is stable for identical reference audio.

    Gradio uploads every request to a fresh temp directory, so the same
    reference file arrives under a different path each line.  IndexTTS2
    caches speaker/style features by path; normalising by content hash lets
    the cache hit across lines (and across the whole video).
    """
    try:
        digest = hashlib.sha256(Path(prompt).read_bytes()).hexdigest()
        dest = REF_CACHE / f"{digest}.wav"
        if not dest.exists():
            REF_CACHE.mkdir(parents=True, exist_ok=True)
            shutil.copy2(prompt, dest)
            print(f"[shim] new stable reference: {dest}", flush=True)
        return str(dest)
    except Exception:
        return prompt

# IndexTTS2 writes results into its own out_dir, outside this app's sandbox.
# Fetch it from the API so Gradio is allowed to serve those files.
try:
    _health = requests.get(f"{API}/health", timeout=10).json()
    OUT_DIR = _health.get("out_dir") or os.environ.get("INDEXTTS2_OUT_DIR", "")
except Exception:  # noqa: BLE001
    OUT_DIR = os.environ.get("INDEXTTS2_OUT_DIR", "")


def _itt_cmd(*args: str) -> list[str]:
    # Windows ships .cmd; POSIX ships .sh, which may lose its exec bit when the
    # skill is copied from a Windows host. Invoke it through bash explicitly.
    if os.name == "nt":
        return [ITT_CLI, *args]
    return ["bash", ITT_CLI, *args]


def _maybe_restart() -> None:
    if not ITT_CLI or not RESTART_EVERY or _counter % RESTART_EVERY != 0:
        return
    try:
        subprocess.run(_itt_cmd("stop", "--json"), timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
    except Exception:  # noqa: BLE001
        pass


def gen_single(prompt, text):
    global _counter
    if not prompt:
        raise gr.Error("missing reference audio")
    if not text or not text.strip():
        raise gr.Error("missing text")
    _counter += 1
    prompt = _stable_ref(prompt)
    name = f"bridge_{int(time.time() * 1000)}_{os.getpid()}_{_counter}.wav"
    payload = {"text": text.strip(), "voice": prompt, "output_name": name}
    if FAST:
        payload["fast"] = True
    headers = {"Authorization": f"Bearer {KEY}"} if KEY else {}
    last_error = None
    for _attempt in range(3):
        try:
            resp = requests.post(f"{API}/v1/tts", json=payload, headers=headers, timeout=3600)
            if resp.status_code >= 500:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            if not data.get("ok") or not data.get("output_path"):
                raise RuntimeError(str(data)[:500])
            _maybe_restart()
            return data["output_path"]
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2)
    raise gr.Error(f"IndexTTS2 failed after retries: {last_error}")


with gr.Blocks(title="IndexTTS2 bridge") as demo:
    ref = gr.Audio(type="filepath", label="prompt")
    txt = gr.Textbox(label="text")
    out = gr.Audio(type="filepath", label="output")
    btn = gr.Button("generate")
    btn.click(gen_single, inputs=[ref, txt], outputs=out, api_name="gen_single")

demo.queue()
demo.launch(server_name="127.0.0.1", server_port=int(os.environ.get("SHIM_PORT", "7861")),
            show_api=True, quiet=True, show_error=True,
            allowed_paths=[OUT_DIR] if OUT_DIR else None)
