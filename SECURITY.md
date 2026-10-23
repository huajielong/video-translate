# Security

## Local services

`pipeline.py serve` starts the IndexTTS2 API on `127.0.0.1:8791` and the Gradio
shim on `127.0.0.1:7861`. They bind to localhost only and have no
authentication. Do not expose these ports to a public network. If you need
remote access, put them behind an authenticated reverse proxy and a firewall.

## Secrets

- DeepSeek and other API keys are written to the local
  `vendor/pyvideotrans/videotrans/params.json` or supplied through environment
  variables. That file is gitignored.
- `config.json` is local machine state and is gitignored.
- Do not paste keys into issues, logs, prompts, screenshots, or commit
  messages.
- Before publishing, run a secret scan over the full Git history, for example:
  `gitleaks detect --source . --redact`.

## Untrusted media

ASR, translation, and TTS process untrusted video/audio/subtitle input. Keep
input files outside the output directory; the pipeline's `--no-clear-cache`
handling assumes that separation. Do not run the pipeline as an administrator.

## Reporting

If you find a vulnerability, open a private security advisory on the GitHub
repository rather than a public issue.
