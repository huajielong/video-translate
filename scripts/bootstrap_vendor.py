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
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vendor_paths import vendor_root  # noqa: E402

# Installed runtimes live outside the skill tree (see vendor_paths.py); the
# per-user root is overridable with VTV_VENDOR_DIR / setup --vendor-dir.
VENDOR = vendor_root()
PVT = VENDOR / "pyvideotrans"
ITT = VENDOR / "index-tts2-cli"
WRAPPER = SKILL / "wrapper" / "index-tts2-cli"
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
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.bootstrap-tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
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


def install_pyvideotrans(dest: Path) -> None:
    if (dest / "cli.py").is_file():
        log("pyVideoTrans already present")
        return
    if dest.exists():
        raise RuntimeError(
            f"{dest} exists but is incomplete; remove it or use --reuse-pvt"
        )
    log(f"cloning pyVideoTrans {PVT_TAG}")
    tmp = _clone(PVT_URL, PVT_TAG, dest)
    try:
        _normalize(tmp, PVT_FILES)
        _apply(tmp, PATCHES / "pyvideotrans.patch")
        tmp.rename(dest)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def install_index_tts(dest: Path) -> None:
    if (dest / "scripts" / "indextts2.py").is_file() and (dest / "indextts").is_dir():
        log("IndexTTS2 CLI already present")
        return
    if dest.exists():
        raise RuntimeError(
            f"{dest} exists but is incomplete; remove it or use --reuse-itt"
        )
    if not WRAPPER.is_dir():
        raise RuntimeError(f"wrapper source missing: {WRAPPER}")
    log(f"cloning IndexTTS2 {ITT_TAG}")
    tmp = _clone(ITT_URL, ITT_TAG, dest)
    try:
        for child in WRAPPER.iterdir():
            target = tmp / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)
        _normalize(tmp, ITT_FILES)
        _apply(tmp, PATCHES / "index-tts.patch")
        tmp.rename(dest)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def ensure_vendor(install_pvt: bool = True, install_itt: bool = True,
                  vendor: str | os.PathLike | None = None) -> None:
    root = Path(vendor).expanduser().resolve() if vendor else VENDOR
    root.mkdir(parents=True, exist_ok=True)
    pvt = root / "pyvideotrans"
    itt = root / "index-tts2-cli"
    if install_pvt:
        install_pyvideotrans(pvt)
    if install_itt:
        install_index_tts(itt)
    if install_pvt and not (pvt / "cli.py").is_file():
        raise RuntimeError("pyVideoTrans bootstrap did not produce cli.py")
    if install_itt and not (itt / "scripts" / "indextts2.py").is_file():
        raise RuntimeError("IndexTTS2 bootstrap did not produce scripts/indextts2.py")
    log(f"vendor ready: {root}")


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
