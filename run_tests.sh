#!/usr/bin/env bash
# Run the whole test suite, including the 70,000-message simulation.
set -euo pipefail
cd "$(dirname "$0")"

UV="${UV:-uv}"
if ! command -v "$UV" >/dev/null 2>&1; then
  UV="$HOME/.local/bin/uv"
fi

exec "$UV" run python -m unittest discover -s tests -t . -v
