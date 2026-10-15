#!/usr/bin/env bash
# Runs as root (systemd ExecStartPre=+): dump the database from the db container into the
# backup user's state directory, and check the archive is readable. Needs Docker, never the
# Proton session.
set -euo pipefail
: "${VELOTHEQUE_DIR:?set VELOTHEQUE_DIR to the repo checkout}"
STATE_DIR="${STATE_DIR:-/var/lib/velotheque-backup}"
BACKUP_USER="${BACKUP_USER:-velobackup}"
KEEP_LOCAL="${KEEP_LOCAL_DUMPS:-2}"

compose() { docker compose --project-directory "$VELOTHEQUE_DIR" "$@"; }

dumps="$STATE_DIR/dumps"
mkdir -p "$dumps"
rm -f "$STATE_DIR/last-run.json"

name="velotheque-$(date -u +%Y-%m-%dT%H%MZ).dump"
partial="$dumps/.$name.part"
trap 'rm -f "$partial"' EXIT

compose exec -T db sh -c 'pg_dump --format=custom -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$partial"
compose exec -T db pg_restore --list < "$partial" > /dev/null
mv "$partial" "$dumps/$name"

if id "$BACKUP_USER" > /dev/null 2>&1; then
  chown -R "$BACKUP_USER:$BACKUP_USER" "$dumps"
fi
chmod 700 "$dumps"
chmod 600 "$dumps/$name"

ls -1t "$dumps"/velotheque-*.dump | tail -n "+$((KEEP_LOCAL + 1))" | while read -r old; do
  rm -f -- "$old"
done

echo "$dumps/$name ($(du -h "$dumps/$name" | cut -f1))"
