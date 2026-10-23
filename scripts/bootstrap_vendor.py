#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Download the third-party runtimes used by the skill on first use.

The repository deliberately does not contain pyVideoTrans, IndexTTS2,
Rubberband, model weights, or checkpoints.  This module clones the pinned
upstream versions, overlays the small IndexTTS2 CLI wrapper, and applies the
project's patch files.  Model/venv setup is handled by pipeline.py setup.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
VENDOR = SKILL / "vendor"
PVT = VENDOR / "pyvideotrans"
ITT = VENDOR / "index-tts2-cli"
WRAPPER = SKILL / "vendor_src" / "index-tts2-cli"
PATCHES = SKILL / "patches"

PVT_URL = "https://github.com/jianchang512/pyvideotrans.git"
PVT_TAG = "v4.16"
ITT_URL = "https://github.com/index-tts/index-tts.git"
ITT_TAG = "v2.0.0"

PVT_FILES = [
    "videotrans/task/_rate.py",
    "videotrans/task/_stage_align.py",
    "videotrans/task/_stage_dubbing.py",
    "videotrans/task/_stage_subtitle.py",
    "videotrans/tts/_index.py",
    "videotrans/configure/_paths.py",
]
ITT_FILES = [
    "indextts/infer_v2.py",
    "indextts/gpt/model_v2.py",
    "indextts/s2mel/modules/commons.py",
    "indextts/s2mel/modules/diffusion_transformer.py",
    "indextts/s2mel/modules/flow_matching.py",
    "indextts/utils/common.py",
    "indextts/utils/front.py",
]


def log(message: str) -> None:
    print(f"[bootstrap] {message}", flush=True)


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    log(" ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)


def _clone(url: str, tag: str, dest: Path) -> Path:
    if shutil.which("git") is None:
        raise RuntimeError(
            "git is required for the automatic vendor download; install git or "
            "pass setup --reuse-pvt/--reuse-itt with an existing installation"
        )
    tmp = VENDOR / f".{dest.name}.bootstrap-tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    VENDOR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # IndexTTS2 keeps example WAVs in Git LFS; the wrapper does not need them.
    env.setdefault("GIT_LFS_SKIP_SMUDGE", "1")
    _run(["git", "clone", "--depth", "1", "--branch", tag, url, str(tmp)], env=env)
    shutil.rmtree(tmp / ".git", ignore_errors=True)
    return tmp


def _normalize(root: Path, rels: list[str]) -> None:
    for rel in rels:
        path = root / rel
        if not path.is_file():
            raise RuntimeError(f"upstream file missing: {path}")
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))


def _apply(root: Path, patch: Path) -> None:
    if not patch.is_file():
        raise RuntimeError(f"patch missing: {patch}")
    _run(["git", "apply", "--whitespace=nowarn", str(patch)], cwd=root)


def install_pyvideotrans() -> None:
    if (PVT / "cli.py").is_file():
        log("pyVideoTrans already present")
        return
    if PVT.exists():
        raise RuntimeError(
            f"{PVT} exists but is incomplete; remove it or use --reuse-pvt"
        )
    log(f"cloning pyVideoTrans {PVT_TAG}")
    tmp = _clone(PVT_URL, PVT_TAG, PVT)
    try:
        _normalize(tmp, PVT_FILES)
        _apply(tmp, PATCHES / "pyvideotrans.patch")
        tmp.rename(PVT)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def install_index_tts() -> None:
    if (ITT / "scripts" / "indextts2.py").is_file() and (ITT / "indextts").is_dir():
        log("IndexTTS2 CLI already present")
        return
    if ITT.exists():
        raise RuntimeError(
            f"{ITT} exists but is incomplete; remove it or use --reuse-itt"
        )
    if not WRAPPER.is_dir():
        raise RuntimeError(f"wrapper source missing: {WRAPPER}")
    log(f"cloning IndexTTS2 {ITT_TAG}")
    tmp = _clone(ITT_URL, ITT_TAG, ITT)
    try:
        for child in WRAPPER.iterdir():
            target = tmp / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)
        _normalize(tmp, ITT_FILES)
        _apply(tmp, PATCHES / "index-tts.patch")
        tmp.rename(ITT)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def ensure_vendor(install_pvt: bool = True, install_itt: bool = True) -> None:
    VENDOR.mkdir(parents=True, exist_ok=True)
    if install_pvt:
        install_pyvideotrans()
    if install_itt:
        install_index_tts()
    if install_pvt and not (PVT / "cli.py").is_file():
        raise RuntimeError("pyVideoTrans bootstrap did not produce cli.py")
    if install_itt and not (ITT / "scripts" / "indextts2.py").is_file():
        raise RuntimeError("IndexTTS2 bootstrap did not produce scripts/indextts2.py")
    log("vendor ready")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pvt-only", action="store_true")
    parser.add_argument("--itt-only", action="store_true")
    args = parser.parse_args()
    try:
        ensure_vendor(
            install_pvt=not args.itt_only,
            install_itt=not args.pvt_only,
        )
        return 0
    except Exception as exc:
        log(f"failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
