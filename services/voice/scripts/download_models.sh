#!/usr/bin/env bash
# Fetch the voice models into services/voice/models/ (see download_models.py for the rules).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
exec uv run --no-sync python "$here/download_models.py" "$@"
