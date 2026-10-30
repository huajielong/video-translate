#!/usr/bin/env bash
# Thin alias for "indextts2.sh api".
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$SCRIPT_DIR/indextts2.sh" api "$@"
