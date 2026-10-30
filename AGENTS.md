# video-translate

This repository is a portable video translation/dubbing skill. Read
[`SKILL.md`](SKILL.md) first; it is the detailed operating manual.

## Quick commands

```bash
# First use auto-downloads pyVideoTrans/IndexTTS2 and models.
python scripts/pipeline.py setup        # first install
python scripts/pipeline.py doctor       # self-check
python scripts/pipeline.py serve        # IndexTTS2 API + Gradio shim
python scripts/pipeline.py run --src "D:/videos" --out "D:/translated" \
  --source en --target zh-cn --backend index
python scripts/pipeline.py report --out "D:/translated"
```

On Windows, use `pythonw scripts/pipeline.pyw <command>` when the parent
process has no console, so no cmd/conhost window flashes.

## First run: remind the user

Before the first `run`, ask the user to configure the DeepSeek key; do not ask
them to paste it into chat. The key belongs in
`<pyvideotrans_home>/videotrans/params.json` (default
`vendor/pyvideotrans/videotrans/params.json`), not `config.json`.

```bash
python scripts/pipeline.py setup --deepseek-key "sk-..."
# or set DEEPSEEK_API_KEY and rerun setup
```

`params.example.json` is a safe template. `run`/`prep` exit early with a clear
message when the key is missing; `doctor` also warns.

Confirm these before starting:

- `source_lang` / `target_lang` (default `en` / `zh-cn`)
- `subtitle_mode` (`hard` or `soft`)
- `subtitle_max_chars` (22 for CJK; non-CJK uses `other_len=60`); `subtitle_fit_frame`
  tightens it per video so a 960x1080/portrait frame does not overflow
- `voice_autorate=false`, `video_autorate=true`
- `max_video_speedup_rate=1.25`, `max_phrase_pause_ms=300`
- `duration_tol_s=15`
- `resegment_llm_check=true`
- `clone_ref_mode=line`
- `glossary` for the course/domain
- `--src` / `--out` (or `src_dir` / `out_dir`) and free disk

## Vendor dependencies

This repository does not include `vendor/`, model weights, checkpoints, API
keys, or local config. On first use, `setup` (or a missing `run`/`dub`/`serve`/
`prep`) automatically clones pyVideoTrans v4.16 and IndexTTS2 v2.0.0, applies
the project patches, and then downloads models. It needs git, network access,
and about 20GB+ free disk.

To reuse an existing vendor installation instead:

```bash
python scripts/pipeline.py setup \
  --reuse-pvt /path/to/pyvideotrans \
  --reuse-itt /path/to/index-tts2-cli
```

Set `VTV_AUTO_INSTALL=0` to disable automatic downloading. See
`references/deployment.md`, `patches/README.md`, and `THIRD_PARTY_NOTICES.md`.

## Rules

- Run `doctor` before a task; run `setup` on a fresh machine.
- Do not put input videos in the output directory.
- Keep SRT files in `<out>/_字幕/`, not next to the final MP4.
- IndexTTS2 is a single worker; do not run two dubbing jobs at once.
- Do not commit `config.json`, `params.json`, keys, media, models, venvs, or
  `.runtime/`.
- Do not modify `vendor/` unless you are maintaining the patch set; prefer
  `config.json` or CLI options.
