# -*- coding: utf-8 -*-
"""Auto-applied to Python children launched by video-translate.

``pipeline.py`` prepends this directory to PYTHONPATH, so the IndexTTS2 API
process (and anything it spawns) also suppresses console windows.  Keep this
file self-contained: it must work before any skill module is importable.
"""
from __future__ import annotations

import os
import subprocess

if os.name == "nt" and not getattr(subprocess, "_vtv_no_window", False):
    _popen = subprocess.Popen
    _no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    _keep = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
             | getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010))

    class _HiddenPopen(_popen):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            flags = int(kwargs.get("creationflags") or 0)
            if not flags & _keep:
                kwargs["creationflags"] = flags | _no_window
            super().__init__(*args, **kwargs)

    subprocess.Popen = _HiddenPopen  # type: ignore[assignment,misc]
    subprocess._vtv_no_window = True  # type: ignore[attr-defined]
