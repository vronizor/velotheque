#!/usr/bin/env bash
# Runs as root (systemd ExecStopPost=+), after success or failure: turn last-run.json into a
# backup_status row so the app's header indicator reflects it.
set -uo pipefail
: "${VELOTHEQUE_DIR:?set VELOTHEQUE_DIR to the repo checkout}"
STATE_DIR="${STATE_DIR:-/var/lib/velotheque-backup}"
result="$STATE_DIR/last-run.json"

compose() { docker compose --project-directory "$VELOTHEQUE_DIR" "$@"; }
field() { python3 -c 'import json, sys; print(json.dumps(json.load(open(sys.argv[1])).get(sys.argv[2])) if sys.argv[2] == "details" else json.load(open(sys.argv[1])).get(sys.argv[2]) or "")' "$result" "$1"; }

if [ "${SERVICE_RESULT:-success}" = "success" ] && [ -s "$result" ] && [ "$(field ok)" = "True" ]; then
  compose exec -T app velotheque backup-status --ok --details "$(field details)"
else
  message=""
  [ -s "$result" ] && message="$(field error)"
  [ -n "$message" ] || message="backup service result ${SERVICE_RESULT:-unknown} (exit ${EXIT_STATUS:-?}); see journalctl -u velotheque-backup"
  compose exec -T app velotheque backup-status --error "$message"
fi
