#!/usr/bin/env pythonw
# -*- coding: utf-8 -*-
"""Register/remove the GPU-clock logon task without a console window.

    pythonw scripts\\gpu_lock_task.pyw           # register + start
    pythonw scripts\\gpu_lock_task.pyw remove    # delete + reset clocks

Run it as Administrator.  schtasks and nvidia-smi are started with
CREATE_NO_WINDOW, and the result is shown in a GUI message box, so the
launcher never flashes a cmd window.
"""
import ctypes
import subprocess
import sys

TASK = "video-translate-gpu-clock"
NVIDIA = r"C:\Windows\System32\nvidia-smi.exe"
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          creationflags=FLAGS)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1].lower() == "remove":
        run(["schtasks", "/Delete", "/TN", TASK, "/F"])
        run([NVIDIA, "-rgc"])
        text = f"Removed {TASK} and reset GPU clocks."
        icon, rc = 0x40, 0
    else:
        proc = run(["schtasks", "/Create", "/TN", TASK, "/TR",
                    f"{NVIDIA} -lgc 300,1200", "/SC", "ONLOGON",
                    "/RL", "HIGHEST", "/F"])
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            text = f"Failed to create {TASK}. Run as Administrator.\n\n{detail}"
            icon, rc = 0x10, 1
        else:
            run(["schtasks", "/Run", "/TN", TASK])
            text = (f"Registered and started {TASK}.\n\n"
                    r"To undo: pythonw scripts\gpu_lock_task.pyw remove")
            icon, rc = 0x40, 0
    ctypes.windll.user32.MessageBoxW(0, text, "video-translate GPU clock", icon)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
