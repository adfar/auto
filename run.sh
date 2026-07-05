#!/usr/bin/env bash
# Full daily cycle: scan -> forecast top N -> paper trade -> settle & score.
set -euo pipefail
cd "$(dirname "$0")"
N="${1:-10}"

# Prefer the project venv (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)
PY="python3"
[[ -x .venv/bin/python ]] && PY=".venv/bin/python"

"$PY" scan.py
"$PY" forecast.py "$N"
"$PY" trade.py
"$PY" settle.py
