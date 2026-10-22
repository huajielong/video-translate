#!/usr/bin/env pythonw
# -*- coding: utf-8 -*-
"""Windowless pipeline entry point.

Run with pythonw.exe so Windows never allocates a console for the interpreter:

    pythonw scripts/pipeline.pyw run --src ... --out ...

Progress and tracebacks go to .runtime/logs/entry.log because pythonw has no
stdout.  The normal `python scripts/pipeline.py` command still works and keeps
printing to the terminal.
"""
import runpy
import sys
from pathlib import Path

script = Path(__file__).resolve().with_name("pipeline.py")
sys.argv[0] = str(script)
runpy.run_path(str(script), run_name="__main__")
