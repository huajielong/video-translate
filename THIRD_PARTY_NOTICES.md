# Third-party notices

This repository contains the original `video-translate` skill code and small
patch/wrapper sources needed to reconstruct a compatible vendor installation.
It does **not** redistribute the full pyVideoTrans, IndexTTS2, Rubberband,
model weights, or checkpoints. On first use, `bootstrap_vendor.py` downloads
the pinned upstream versions and applies `patches/`; each dependency remains
under its own license.

The patches and wrapper are derived works under the upstream terms:

- `patches/pyvideotrans.patch` — GPL-3.0 (`patches/LICENSE.pyvideotrans`)
- `patches/index-tts.patch` and `vendor_src/index-tts2-cli/` — bilibili Model
  Use License (`patches/LICENSE.index-tts`, `patches/NOTICE.index-tts.md`)

The upstream versions are pyVideoTrans `v4.16` and IndexTTS2 `v2.0.0`.

## pyVideoTrans

- Upstream: <https://github.com/jianchang512/pyvideotrans>
- License: **GNU GPL-3.0** (`vendor/pyvideotrans/LICENSE` in a local install)
- The skill launches pyVideoTrans as a separate program. If you redistribute a
  combined package that includes pyVideoTrans, comply with GPL-3.0 and provide
  the corresponding source.

## IndexTTS2 / IndexTTS2 CLI

- Upstream model/code: <https://github.com/index-tts/index-tts>
- License: **bilibili Model Use License Agreement**
  (`vendor/index-tts2-cli/LICENSE` and `LICENSE_ZH.txt` in a local install)
- This is not an OSI-approved open-source license. It includes restrictions for
  products with more than 100 million monthly active users or annual revenue
  above RMB 1 billion. Review the upstream agreement before commercial use.
- Model weights and checkpoints are downloaded separately and are not included
  in this repository.
- The community `index-tts2-cli` wrapper is distributed under the same terms
  unless its own notice says otherwise.

## Rubberband

- Upstream: <https://breakfastquay.com/rubberband/>
- License: **GPL-2.0** for the bundled Windows executable.
- This repository does not include the executable. Install Rubberband through
  your package manager, or obtain a GPL-compliant source distribution from the
  upstream project.

## FFmpeg

- Upstream: <https://ffmpeg.org/>
- FFmpeg is installed by the user/system. Its build may be LGPL-2.1+ or GPL,
  depending on the enabled components; check `ffmpeg -version`.

## Fonts

Do not commit proprietary fonts. If you add a font, use an OFL/Apache-licensed
font such as Noto Sans CJK or Source Han Sans, and include its license.

## DeepSeek and other APIs

API usage is governed by the provider's terms. API keys belong in the local
`params.json` or environment, never in this repository.
