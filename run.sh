#!/usr/bin/env bash
# /opt/ice/mol-witness/run.sh
set -euo pipefail
cd "$(dirname "$0")"

export GITHUB_REPOSITORY="ilupu/mol-witness"          # owner/repo
export GITHUB_TOKEN="$(cat .github_token)"           # PAT, chmod 600
export GITHUB_RUN_ID="cron-$(hostname)-$(date -u +%Y%m%dT%H%M%SZ)"

LOG=logs/witness.log
mkdir -p logs
{
  echo "=== $(date -u +%FT%TZ) start ==="
  timeout 600 python3 witness.py
  echo "=== $(date -u +%FT%TZ) exit=$? ==="
} >>"$LOG" 2>&1
