# -*- coding: utf-8 -*-
"""Keep Windows console children from flashing a cmd/PowerShell window.

A console-subsystem child (python.exe, uv.exe, ffmpeg.exe, cmd.exe running a
.cmd shim) gets a NEW console window whenever its parent has no console of its
own.  That is exactly the situation when an editor/agent drives the skill, and
redirecting stdout/stderr does NOT prevent the window.  CREATE_NO_WINDOW does.

Call ``hide_console_windows()`` once at process start.  It patches
``subprocess.Popen``, so it covers every later ``subprocess.run/Popen/call`` in
this process, including vendor code that imports the same module.  Launchers
that deliberately pass DETACHED_PROCESS or CREATE_NEW_CONSOLE are left alone
(Windows rejects CREATE_NO_WINDOW combined with them).
"""
from __future__ import annotations

import os
import subprocess

_applied = False


def hide_console_windows() -> None:
    global _applied
    if _applied or os.name != "nt":
        return

    _popen = subprocess.Popen
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    new_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
    keep_flags = detached | new_console

    class _HiddenPopen(_popen):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            flags = int(kwargs.get("creationflags") or 0)
            if not flags & keep_flags:
                kwargs["creationflags"] = flags | no_window
            super().__init__(*args, **kwargs)

    subprocess.Popen = _HiddenPopen  # type: ignore[assignment,misc]
    _applied = True
