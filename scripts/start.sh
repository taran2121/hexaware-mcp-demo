#!/usr/bin/env bash
# One-command start for the Tracker agent demo (macOS / Linux).
# Creates .venv on first run, resets the demo data, then serves http://localhost:8600
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  echo "Creating .venv and installing requirements (first run only)..." >&2
  python3 -m venv .venv
  .venv/bin/python -m pip install -q -r requirements.txt
fi

.venv/bin/python scripts/reset_data.py
exec .venv/bin/python agent/app.py
