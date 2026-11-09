#!/usr/bin/env bash
# Start the G-IPTV server using the project virtualenv.
# Works from any directory; prefers the venv created by install.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$ROOT/server/.venv/bin/python"

if [[ -x "$VENV_PY" ]]; then
  exec "$VENV_PY" "$ROOT/server/main.py" "$@"
else
  echo "virtualenv not found — run:  bash server/install.sh" >&2
  echo "falling back to system python3" >&2
  exec python3 "$ROOT/server/main.py" "$@"
fi
