# Changelog

## 2026-10-30

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
