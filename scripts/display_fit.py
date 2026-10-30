#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Per-video character budget for one-line burned-in subtitles.

ffmpeg converts SRT to ASS with ``PlayResX/PlayResY = 384/288`` and font size
16; libass then scales glyphs by ``frame_height / 288``.  A fixed 22-character
line therefore fits a 16:9 frame but runs past the edges of narrower frames
(640x720, 960x1080, portrait phone captures).  The budget below is derived from
the real frame size and the ASS style so every display cue fits in the video.

A CJK glyph is exactly one em wide and no Latin glyph exceeds one em, so
``usable_pixels / font_pixels`` is a safe upper bound on characters per line
for any text.  The caller turns it into the ``VTV_CJK_LEN`` /
``subtitle_max_chars`` value used by the aligner and the subtitle burner.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ffmpeg's default ASS header (libavcodec/ass.c) for SRT input.
PLAYRES_X = 384
PLAYRES_Y = 288
ASS_DEFAULTS = {"Fontsize": 16, "MarginL": 10, "MarginR": 10, "Outline": 1}

DEFAULT_CAP = 22
DEFAULT_SAFETY = 0.95
DEFAULT_FLOOR = 8


def load_ass_style(home: str | os.PathLike | None = None) -> dict:
    """Read the pyVideoTrans hard-subtitle style overrides, if any.

    ``videotrans/ass.json`` is written by pyVideoTrans' style dialog.  Without
    it the ffmpeg defaults above apply, which is the normal case for this
    skill.
    """
    home = home or os.environ.get("PYVIDEOTRANS_HOME", "")
    if not home:
        return {}
    path = Path(home) / "videotrans" / "ass.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return {k: data[k] for k in ASS_DEFAULTS if k in data}


def frame_char_limit(width: int, height: int, style: dict | None = None,
                     cap: int = DEFAULT_CAP,
                     safety: float = DEFAULT_SAFETY,
                     floor: int = DEFAULT_FLOOR,
                     cjk: bool = True) -> int:
    """Characters that fit one subtitle line in a ``width x height`` frame.

    ``cap`` keeps the configured ``subtitle_max_chars`` as the design maximum;
    ``safety`` reserves a small inset so a glyph never touches the frame edge.
    ``cjk=False`` budgets roughly half an em per character for Latin text.
    """
    if width <= 0 or height <= 0:
        return cap
    st = {**ASS_DEFAULTS, **(style or {})}
    try:
        font_px = float(st["Fontsize"]) * height / PLAYRES_Y
        margin_px = (float(st["MarginL"]) + float(st["MarginR"])) * width / PLAYRES_X
        outline_px = 2.0 * float(st["Outline"]) * height / PLAYRES_Y
    except (TypeError, ValueError):
        return cap
    if font_px <= 0:
        return cap
    usable = width * safety - margin_px - outline_px
    em_per_char = 1.0 if cjk else 0.5
    limit = int(usable // (font_px * em_per_char))
    limit = max(int(floor), limit)
    if cap > 0:
        limit = min(limit, int(cap))
    return limit


CJK_TARGETS = ("zh", "ja", "ko")


def display_limit(width: int, height: int, cfg: dict) -> int:
    """Apply the config switches to :func:`frame_char_limit`."""
    cap = int(cfg.get("subtitle_max_chars", DEFAULT_CAP) or DEFAULT_CAP)
    if not cfg.get("subtitle_fit_frame", True):
        return cap
    try:
        safety = float(cfg.get("subtitle_safety", DEFAULT_SAFETY))
    except (TypeError, ValueError):
        safety = DEFAULT_SAFETY
    target = str(cfg.get("target_lang") or "zh-cn").lower()
    if target[:2] not in CJK_TARGETS:
        # pyVideoTrans measures non-CJK lines with `other_len` (60) instead of
        # VTV_CJK_LEN, so there is nothing for this override to change.
        return cap
    return frame_char_limit(width, height,
                            load_ass_style(cfg.get("pyvideotrans_home")),
                            cap=cap, safety=safety)
