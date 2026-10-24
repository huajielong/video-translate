# Changelog

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
  small IndexTTS2 CLI wrapper from `vendor_src/`.
