#!/usr/bin/env bash
# Install this skill into a Claude Code skills directory.
# Usage: ./install.sh [destination]
set -euo pipefail

src="$(cd "$(dirname "$0")" && pwd)"
dest="${1:-$HOME/.claude/skills/video-translate}"

mkdir -p "$dest"
if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude '.git/' \
    --exclude 'vendor/' \
    --exclude 'tools/rubberband/' \
    --exclude '.runtime/' \
    --exclude 'config.json' \
    --exclude '__pycache__/' \
    "$src/" "$dest/"
else
  tar -C "$src" \
    --exclude='.git' \
    --exclude='vendor' \
    --exclude='tools/rubberband' \
    --exclude='.runtime' \
    --exclude='config.json' \
    --exclude='__pycache__' \
    -cf - . | tar -C "$dest" -xf -
fi

echo "Installed to: $dest"
echo "Next:"
echo "  cd \"$dest\""
echo "  python scripts/pipeline.py setup --reuse-pvt /path/to/pyvideotrans --reuse-itt /path/to/index-tts2-cli"
echo "  python scripts/pipeline.py doctor"
