# video-translate

This repository is a portable video translation/dubbing skill. Read
[`SKILL.md`](SKILL.md) first; it is the detailed operating manual.

## Quick commands

```bash
# First put pyVideoTrans/IndexTTS2 in vendor/, or add --reuse-pvt/--reuse-itt.
python scripts/pipeline.py setup        # first install
python scripts/pipeline.py doctor       # self-check
python scripts/pipeline.py serve        # IndexTTS2 API + Gradio shim
python scripts/pipeline.py run --src "D:/videos" --out "D:/translated" \
  --source en --target zh-cn --backend index
python scripts/pipeline.py report --out "D:/translated"
```

On Windows, use `pythonw scripts/pipeline.pyw <command>` when the parent
process has no console, so no cmd/conhost window flashes.

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
