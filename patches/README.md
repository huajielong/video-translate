# Vendor patches

`bootstrap_vendor.py` downloads these upstream versions and applies the patches
below. The patched code remains under the upstream licenses; the patch files are
derived works and are included for reproducibility.

## pyVideoTrans

- Upstream: `https://github.com/jianchang512/pyvideotrans.git`
- Tag: `v4.16`
- Patch: `pyvideotrans.patch`
- License of patched code: GPL-3.0 (`LICENSE.pyvideotrans`)
- Modified paths:
  - `videotrans/task/_rate.py`
  - `videotrans/task/_stage_align.py`
  - `videotrans/task/_stage_dubbing.py`
  - `videotrans/task/_stage_subtitle.py`
  - `videotrans/tts/_index.py`
  - `videotrans/configure/_paths.py`

`_stage_dubbing.py` is a full-file replacement patch because the local
audio-native/fit implementation is substantially larger than upstream v4.16.
Bootstrap normalizes patched text files to LF before applying; do the same if
you apply the patch manually.

`_rate.py` also pins the placeholder silence (`silent_place_{i}.wav`) to the
project sample rate and channel count. Upstream used pydub's default
11025 Hz mono; when such a placeholder was the first concat input, ffmpeg
interpreted every later 48 kHz stereo clip with its time base and multiplied
the whole track by 8.707 (source 315 s → output 2356 s).

## IndexTTS2 + wrapper

- Upstream model/code: `https://github.com/index-tts/index-tts.git`
- Tag: `v2.0.0`
- Patch: `index-tts.patch`
- Wrapper source: `wrapper/index-tts2-cli/`
- License: bilibili Model Use License (`LICENSE.index-tts`,
  `NOTICE.index-tts.md`); not OSI open source
- Modified paths:
  - `indextts/infer_v2.py`
  - `indextts/gpt/model_v2.py`
  - `indextts/s2mel/modules/commons.py`
  - `indextts/s2mel/modules/diffusion_transformer.py`
  - `indextts/s2mel/modules/flow_matching.py`
  - `indextts/utils/common.py`
  - `indextts/utils/front.py`

The wrapper in `wrapper/index-tts2-cli/` is laid over the upstream checkout;
it provides the HTTP daemon/API used by the skill.

## Applying manually

`$VENDOR` below is the install root (`<vendor 根>`). It defaults to a per-user
data directory **outside the skill** — Windows `%LOCALAPPDATA%\video-translate\vendor`,
Linux `$XDG_DATA_HOME/video-translate/vendor`, macOS
`~/Library/Application Support/video-translate/vendor` — and can be overridden
with `VTV_VENDOR_DIR` (see `references/deployment.md`).

```bash
# pyVideoTrans
VENDOR="${VTV_VENDOR_DIR:-$HOME/.local/share/video-translate/vendor}"
git clone --depth 1 --branch v4.16 \
  https://github.com/jianchang512/pyvideotrans.git "$VENDOR/pyvideotrans"
# normalize the six patched files to LF first
git apply --whitespace=nowarn patches/pyvideotrans.patch
```

```bash
# IndexTTS2
git clone --depth 1 --branch v2.0.0 \
  https://github.com/index-tts/index-tts.git "$VENDOR/index-tts2-cli"
cp -a wrapper/index-tts2-cli/. "$VENDOR/index-tts2-cli/"
# normalize the seven patched files to LF first
git apply --whitespace=nowarn patches/index-tts.patch
```

Then run `python scripts/pipeline.py setup` to create venvs and download models.
