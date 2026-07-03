#!/usr/bin/env bash
# Full daily cycle: scan -> forecast top N -> paper trade -> settle & score.
set -euo pipefail
cd "$(dirname "$0")"
N="${1:-10}"

python3 scan.py
python3 forecast.py "$N"
python3 trade.py
python3 settle.py
