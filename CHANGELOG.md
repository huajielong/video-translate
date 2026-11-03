# Changelog

## 2026-11-03

- Long-transcript resegment audit now chunks cue lists (`resegment_chunk_cues`,
  default 90, sentence-boundary splits) with per-chunk caching. Fixes JSON
  truncation on ~170-cue videos that previously fell back to rule segmentation.
- Over-merged groups from small local models (4+ cues) are mechanically split
  into <=3-block units (`repair_groups`) instead of rejecting the whole reply.
  `pipeline.py` only marks the local provider failed on rc=3 (network), not
  rc=4 (invalid groups), and retries the LLM audit when a cached translation
  is missing its `.tts.srt`.
- `resegment_check.py` retries transient connection resets (WinError 10054) and
  502/503/504 once before falling back to another provider.
- `batch_courses.py clean`: delete each course's `_work` after delivery, gated
  on `batch_status.json` (`ok`, finals == expected, QC all passed); `--dry-run`
  previews. `status` now shows dubbed progress.
- `scripts/prep_check.py`: pre-dub gate for `<source>/<target>.srt` and
  `<target>.tts.srt` presence, glossary recall (>=95%) and oversized TTS units;
  supports `--out` or `--manifest` and returns non-zero on failure.
- UTF-8, line-buffered stdout/stderr in entry scripts (fixes mojibake in
  redirected `--help` output and delayed progress logs).
- README refreshed: batch workflow, prep gate, chunked audit, cleanup, docs
  index; `references/deployment.md` documents PowerShell process lookup for
  Windows (Git Bash `pgrep`/`kill -0` cannot see Windows PIDs).
- README adds a before/after demo from a translated course: side-by-side GIF
  (`assets/demo/before-after.gif`) and a 16s audio comparison MP4; `.gitignore`
  whitelists `assets/demo/`.

## 2026-11-02

- Local-first LLM backends. `translate_channel=auto` (new default) probes
  OpenAI-compatible servers before every `prep`/`run`: `local_llm_api` /
  `VTV_LOCAL_LLM_API`, then `local_llm_candidates` (llama.cpp `:8081`, Ollama
  `:11434`), then `params.json:localllm_api`. A single-model server is used
  directly; multi-model servers require `local_llm_model`.
- If local MT fails for a video, `mt_one` retries once with DeepSeek in `auto`
  mode (explicit `local` never calls the online API).
- Resegment review and `polish` follow the same order
  (`resegment_llm_provider=auto`): local, then DeepSeek, then rule-based cues.
  `resegment_check.py` accepts `VTV_RESEGMENT_API/_MODEL/_KEY`, omits
  DeepSeek-only fields for local endpoints, retries once without
  `response_format`, and exits 3/4 so the pipeline can try the next provider.
- LLM TTS groups longer than `resegment_target_max_chars` are split at sentence
  boundaries with proportional timing instead of becoming one oversized unit.
- `dub` passes `--no-auto-ollama` when the local channel is selected, so the
  wrapper cannot start Ollama and compete with IndexTTS2 for VRAM.
- `doctor` reports the probed local LLM and the resolved MT backend; missing
  DeepSeek keys are only an error when no local LLM is available.

## 2026-10-30

- Add `scripts/audio_probe.py` for isolated "crackle" inside a word:
  `DUBBING_CACHE` keys exclude the sampling seed, so one bad IndexTTS2 take is
  frozen forever and copied by every rebuild. The tool reports 4-10 kHz band
  candidates and (after a cache-cleared rebuild) an A/B comparison marking
  windows that dropped 10x or more, which separates a bad sample from a normal
  plosive/fricative. Documented in `references/troubleshooting.md`.
- Install pyVideoTrans/IndexTTS2 outside the skill directory by default
  (Windows `%LOCALAPPDATA%\video-translate\vendor`, macOS
  `~/Library/Application Support/video-translate/vendor`, Linux
  `$XDG_DATA_HOME/video-translate/vendor`). The agent runtime mirrors the skill
  tree and used to copy the 50GB vendor wholesale. Override with
  `VTV_VENDOR_DIR` or `setup --vendor-dir DIR`; existing configs keep working.
- Rename `vendor_src/` to `wrapper/` (it is the CLI/daemon overlay source, not
  the installed runtimes).

- Fit one-line display subtitles to the real frame width (`subtitle_fit_frame`,
  `subtitle_safety`): a fixed 22-char line overflowed 960x1080/640x720/portrait
  frames because libass scales the ASS font by height/288. `dub` now derives the
  per-video limit from ffprobe dimensions (`scripts/display_fit.py`) and passes it
  as `VTV_CJK_LEN`; 16:9 still uses the 22-char cap.
- QC checks each line against the same frame budget and fails on
  `N subtitle lines exceed X chars (frame width)`.

## 2026-10-23

- Default to audio_native: native-speed TTS, video PTS adjustment, 300ms phrase pauses.
- Add sentence resegmentation with optional DeepSeek TTS-unit audit.
- One-line CJK display subtitles (22 chars), aligned to the final video timeline.
- Trim TTS edges, 20ms fades, and 80ms pre/post silence to remove clicks and gaps.
- QC duration bounds derive from video speed limits; `duration_tol_s` is an absolute floor.
- Trim TTS edge silence on every dubbing path, not only `VTV_TTS_SRT`; fallback/rule-based
  videos no longer accumulate seconds of edge silence and inflate the final duration.
- Sanitize out-of-order, zero-length, and overlapping VTT/SRT cues before resegmentation;
  a single bad speaker cue no longer creates multi-minute slots in the aligner.
- Document the Windows windowless process model and vendor prerequisites.
- Move vendor dependencies out of the Git repository; first use now downloads
  pyVideoTrans v4.16 and IndexTTS2 v2.0.0, applies `patches/`, and installs a
  small IndexTTS2 CLI wrapper from `wrapper/`.
