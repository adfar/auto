#!/usr/bin/env bash
# Nightly cycle for launchd/cron: scan -> forecast -> trade -> settle,
# logged to logs/nightly.log, then best-effort push of DB state to GitHub.
#
# launchd runs with a minimal environment, so PATH must include the claude
# CLI (forecast.py spawns it) and standard tools.
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p logs
exec >> logs/nightly.log 2>&1
echo "=== nightly start $(date -u +%FT%TZ) ==="

if ./run.sh "${KALSHI_EDGE_N:-10}"; then
  echo "run.sh ok"
else
  echo "run.sh FAILED (exit $?)"
fi

# Sync DB state (forecasts, trades, settlements) to GitHub — best effort;
# a failure here must never block the next night's run.
if git pull --rebase --autostash origin main; then
  git add kalshi_edge.db
  if git diff --cached --quiet; then
    echo "no DB changes to commit"
  else
    git commit -m "nightly: snapshot, forecasts, trades, settlements ($(date -u +%F))" \
      -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" \
      && git push origin main \
      && echo "DB state pushed" || echo "commit/push FAILED"
  fi
else
  echo "git pull FAILED; skipping DB sync"
fi

echo "=== nightly end $(date -u +%FT%TZ) ==="
