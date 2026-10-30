#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Where third-party runtimes (pyVideoTrans, IndexTTS2) are installed.

They must live *outside* the skill directory: an agent runtime mirrors the
whole skill tree periodically, and a 50GB vendor inside it was copied wholesale
and filled the disk.  The default is a per-user data directory:

  Windows  %LOCALAPPDATA%\\video-translate\\vendor
  macOS    ~/Library/Application Support/video-translate/vendor
  Linux    $XDG_DATA_HOME/video-translate/vendor (or ~/.local/share/...)

Override with ``VTV_VENDOR_DIR`` or ``setup --vendor-dir``.  Keeping the root
per-user also means two copies of the skill share one model/venv installation.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path

APP_NAME = "video-translate"


def vendor_root(override: str | os.PathLike | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get("VTV_VENDOR_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    system = platform.system().lower()
    if system == "windows":
        base = Path(os.environ.get("LOCALAPPDATA")
                    or Path.home() / "AppData" / "Local")
        return (base / APP_NAME / "vendor").resolve()
    if system == "darwin":
        return (Path.home() / "Library" / "Application Support"
                / APP_NAME / "vendor").resolve()
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return (base / APP_NAME / "vendor").resolve()


def pvt_dir(override: str | os.PathLike | None = None) -> Path:
    return vendor_root(override) / "pyvideotrans"


def itt_dir(override: str | os.PathLike | None = None) -> Path:
    return vendor_root(override) / "index-tts2-cli"
