# -*- coding: utf-8 -*-
"""QC for translated videos: duration, streams, loudness, subtitles, glossary.

Usage:
  python qc.py --video FINAL.mp4 --source SRC.mp4 --zh zh-cn.srt --en en.srt \
               --glossary glossary.txt --json-out report.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _winhide import hide_console_windows  # noqa: E402
from display_fit import load_ass_style, frame_char_limit  # noqa: E402

hide_console_windows()

DURATION_TOL_S = float(os.environ.get("VTV_DURATION_TOL_S", "1.0"))


def duration_bounds(src_dur: float) -> tuple[float, float]:
    """Allowed output length for the configured video_autorate limits.

    Shortening is bounded by max_video_speedup_rate (1.25x -> >=80% of source);
    lengthening by max_video_pts_rate (1.5x -> <=150%).  duration_tol_s stays
    as an absolute floor for short clips; the two directions are asymmetric so
    a lax slowdown bound does not mask an over-shortened output.
    """
    try:
        speedup = float(os.environ.get("VTV_MAX_VIDEO_SPEEDUP_RATE", "1.0") or 1.0)
    except ValueError:
        speedup = 1.0
    try:
        slow = float(os.environ.get("VTV_MAX_VIDEO_PTS_RATE", "1.0") or 1.0)
    except ValueError:
        slow = 1.0
    if src_dur <= 0:
        return 0.0, 0.0
    lower = min(src_dur - DURATION_TOL_S, src_dur / max(1.0, speedup) - 2.0)
    upper = max(src_dur + DURATION_TOL_S, src_dur * max(1.0, slow) + 2.0)
    return max(0.0, lower), upper


LOUDNESS_TARGET = -18.0
LOUDNESS_TOL = 1.0
TRUE_PEAK_MAX = -1.0
MAX_LINES = 1
# Design cap; the real per-video limit is derived from the frame width in
# check_video() so a 960x1080 or portrait video does not get 22-char lines that
# run past the edges.
MAX_CJK_PER_LINE = 22
# pyVideoTrans' `other_len` for non-CJK targets.
MAX_OTHER_PER_LINE = 60
CJK_RE = re.compile(r"[぀-ヿ㐀-鿿가-힯豈-﫿]")


def subtitles_are_cjk(path: str) -> bool:
    """Guess the target script from the cue text (timecodes excluded)."""
    try:
        text = "".join(b["text"] for b in parse_srt(path))
    except Exception:
        return True
    chars = [c for c in text if not c.isspace() and not c.isdigit()]
    if not chars:
        return True
    hits = sum(1 for c in chars if CJK_RE.match(c))
    return hits / len(chars) >= 0.3


GLOSSARY_MIN_RECALL = 0.95


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def ffprobe_json(path: str) -> dict:
    p = run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
             "-of", "json", path])
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:500])
    return json.loads(p.stdout or "{}")


def measure_loudness(path: str) -> tuple[float | None, float | None]:
    p = run(["ffmpeg", "-hide_banner", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"])
    text = (p.stderr or "") + (p.stdout or "")
    m_i = re.search(r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+)\s*LUFS", text)
    m_p = re.search(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+)\s*dBFS", text)
    return (float(m_i.group(1)) if m_i else None,
            float(m_p.group(1)) if m_p else None)


def parse_srt(path: str) -> list[dict]:
    content = Path(path).read_text(encoding="utf-8-sig", errors="replace").strip()
    blocks = re.split(r"\n\s*\n", content)
    out = []
    for b in blocks:
        lines = [x.strip("\ufeff").strip() for x in b.splitlines() if x.strip()]
        if len(lines) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[1])
        if not m:
            continue
        g = list(map(int, m.groups()))
        start = g[0] * 3600000 + g[1] * 60000 + g[2] * 1000 + g[3]
        end = g[4] * 3600000 + g[5] * 60000 + g[6] * 1000 + g[7]
        out.append({"start": start, "end": end, "text": "\n".join(lines[2:]).strip()})
    return out


def estimate_lines(text: str, maxlen: int = 15) -> int:
    """Approximate the skill's balanced display wrap (Latin words atomic)."""
    text = re.sub(r"\s*\n\s*", " ", text).strip()
    if len(text) <= maxlen:
        return 1
    punct = set("，。！？；：、,.!?;:")
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_+.\-/']*|\s+|.", text)

    def greedy(t: str) -> int:
        lines, cur = 1, 0
        for tok in t:
            if tok.isspace():
                continue
            if cur and cur + len(tok) > maxlen:
                lines += 1
                cur = len(tok)
            else:
                cur += len(tok)
        return lines

    if len(text) <= 2 * maxlen + 8:
        mid = len(text) // 2
        lo, hi = max(1, mid - maxlen // 2 - 2), min(len(text) - 1, mid + maxlen // 2 + 2)
        cut = None
        for pos in sorted(range(lo, hi + 1), key=lambda p: abs(p - mid)):
            if text[pos - 1] in punct:
                cut = pos
                break
        if cut is None:
            for pos in sorted(range(lo, hi + 1), key=lambda p: abs(p - mid)):
                if text[pos - 1].isspace():
                    cut = pos
                    break
        # The skill's simple_wrap always returns exactly two lines for text
        # that fits 2*maxlen+8: it falls back to a hard split at the midpoint.
        return 2
    return greedy(text)


def _term_in(term: str, text: str) -> bool:
    """ASCII terms need word boundaries (so `batch` does not match `batching`)."""
    if not term:
        return False
    if re.search(r"[A-Za-z0-9]", term):
        return re.search(
            r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])", text
        ) is not None
    return term in text


def check_subtitles(zh_srt: str, en_srt: str | None, glossary: str | None,
                    max_chars: int = MAX_CJK_PER_LINE) -> dict:
    zh = parse_srt(zh_srt)
    en = parse_srt(en_srt) if en_srt and Path(en_srt).exists() else []
    max_chars = max(1, int(max_chars or MAX_CJK_PER_LINE))
    issues = []
    display_lines = 0
    long_lines = 0
    for i, b in enumerate(zh, 1):
        lines = b["text"].split("\n")
        estimated = (sum(estimate_lines(ln, max_chars) for ln in lines)
                     if lines else 0)
        display_lines = max(display_lines, estimated)
        if estimated > MAX_LINES:
            issues.append(f"block {i}: ~{estimated} display lines "
                          f"(frame allows {max_chars} chars/line)")
        for ln in lines:
            if len(ln) > max_chars:
                long_lines += 1
        if not b["text"].strip():
            issues.append(f"block {i}: empty text")
        if b["end"] <= b["start"]:
            issues.append(f"block {i}: invalid timing")
    texts = [b["text"].strip() for b in zh]
    dup = len(texts) - len(set(texts))
    if dup:
        issues.append(f"{dup} duplicate blocks")

    recall = None
    missing_terms: list[str] = []
    if glossary and Path(glossary).exists() and en:
        en_text = "\n".join(b["text"] for b in en).lower()
        zh_norm = re.sub(r"\s+", "", "\n".join(texts))
        terms = []
        for line in Path(glossary).read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            src, tgt = [x.strip() for x in line.split("=", 1)]
            if src and tgt and _term_in(src.lower(), en_text):
                terms.append((src, tgt))
        if terms:
            zh_lower = "\n".join(texts).lower()
            hit = 0
            missing_terms = []
            for src, tgt in terms:
                # `src=tgt1|tgt2` means any of the listed translations is
                # acceptable (GPU memory vs. agent memory, prompt vs. 提示...).
                variants = [re.sub(r"\s+", "", v) for v in tgt.split("|") if v.strip()]
                # Keeping a technical term in English (LLM, vLLM, token) is
                # acceptable; require either a localized variant or the source.
                if any(v in zh_norm for v in variants) or _term_in(src.lower(), zh_lower):
                    hit += 1
                else:
                    missing_terms.append(src)
            recall = hit / len(terms)
            if recall < GLOSSARY_MIN_RECALL:
                issues.append(f"glossary recall {recall:.1%} < {GLOSSARY_MIN_RECALL:.0%}")

    return {
        "blocks": len(zh),
        "max_chars": max_chars,
        "max_lines": display_lines,
        "long_lines": long_lines,
        "duplicates": dup,
        "glossary_recall": recall,
        "missing_terms": missing_terms[:20],
        "issues": issues,
    }


def check_video(video: str, source: str, zh_srt: str, en_srt: str | None,
                glossary: str | None) -> dict:
    result: dict = {"video_path": video, "ok": False, "errors": [], "warnings": []}
    vp = Path(video)
    if not vp.exists():
        result["errors"].append("video missing")
        return result
    if vp.stat().st_size < 1_000_000:
        result["errors"].append(f"file too small ({vp.stat().st_size} bytes)")

    vinfo = ffprobe_json(video)
    sinfo = ffprobe_json(source) if source and Path(source).exists() else {}
    v_streams = [s for s in vinfo.get("streams", []) if s.get("codec_type") == "video"]
    a_streams = [s for s in vinfo.get("streams", []) if s.get("codec_type") == "audio"]
    if not v_streams:
        result["errors"].append("no video stream")
    else:
        v = v_streams[0]
        result["video"] = {"codec": v.get("codec_name"), "width": v.get("width"),
                           "height": v.get("height"), "pix_fmt": v.get("pix_fmt")}
        if v.get("codec_name") != "h264":
            result["warnings"].append(f"video codec {v.get('codec_name')} != h264")
        if (v.get("width"), v.get("height")) != (1280, 720):
            result["warnings"].append(f"resolution {v.get('width')}x{v.get('height')}")
    if not a_streams:
        result["errors"].append("no audio stream")
    else:
        a = a_streams[0]
        result["audio"] = {"codec": a.get("codec_name"),
                           "sample_rate": int(a.get("sample_rate") or 0),
                           "channels": a.get("channels")}
        if a.get("codec_name") != "aac":
            result["warnings"].append(f"audio codec {a.get('codec_name')} != aac")
        if int(a.get("sample_rate") or 0) != 48000:
            result["errors"].append(f"audio sample rate {a.get('sample_rate')} != 48000")

    out_dur = float(vinfo.get("format", {}).get("duration") or 0)
    src_dur = float(sinfo.get("format", {}).get("duration") or 0) if sinfo else 0
    lower, upper = duration_bounds(src_dur)
    result["duration"] = {"output": round(out_dur, 3), "source": round(src_dur, 3),
                          "diff": round(abs(out_dur - src_dur), 3) if src_dur else None,
                          "min": round(lower, 3), "max": round(upper, 3)}
    if src_dur and not (lower <= out_dur <= upper):
        result["errors"].append(
            f"duration {out_dur:.2f}s outside [{lower:.2f}, {upper:.2f}]")

    if a_streams:
        i, peak = measure_loudness(video)
        result["loudness"] = {"integrated_lufs": i, "true_peak_dbfs": peak}
        if i is None:
            result["warnings"].append("could not measure loudness")
        elif abs(i - LOUDNESS_TARGET) > LOUDNESS_TOL:
            result["errors"].append(f"loudness {i} LUFS outside {LOUDNESS_TARGET}+/-{LOUDNESS_TOL}")
        if peak is not None and peak > TRUE_PEAK_MAX:
            result["warnings"].append(f"true peak {peak} dBFS > {TRUE_PEAK_MAX}")

    if zh_srt and Path(zh_srt).exists():
        # Same width budget the aligner used when it split the display cues,
        # so QC catches lines that would run past the left/right frame edge.
        is_cjk = subtitles_are_cjk(zh_srt)
        cap = MAX_CJK_PER_LINE if is_cjk else MAX_OTHER_PER_LINE
        max_chars = cap
        if v_streams:
            video_stream = v_streams[0]
            width = int(video_stream.get("width") or 0)
            height = int(video_stream.get("height") or 0)
            for side in video_stream.get("side_data_list") or []:
                try:
                    if abs(int(side.get("rotation") or 0)) % 180 == 90:
                        width, height = height, width
                        break
                except (TypeError, ValueError):
                    continue
            max_chars = frame_char_limit(width, height, load_ass_style(),
                                         cap=cap, cjk=is_cjk)
        result["subtitles"] = check_subtitles(zh_srt, en_srt, glossary, max_chars)
        if result["subtitles"]["max_lines"] > MAX_LINES:
            result["errors"].append(f"subtitles have {result['subtitles']['max_lines']} lines")
        if result["subtitles"]["long_lines"]:
            result["errors"].append(
                f"{result['subtitles']['long_lines']} subtitle lines exceed "
                f"{max_chars} chars (frame width)")
    else:
        result["errors"].append("zh subtitle missing")

    result["ok"] = not result["errors"]
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--source", default="")
    ap.add_argument("--zh", required=True)
    ap.add_argument("--en", default="")
    ap.add_argument("--glossary", default="")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    result = check_video(args.video, args.source, args.zh, args.en, args.glossary)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(text, encoding="utf-8")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
