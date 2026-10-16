#!/usr/bin/env bash
# Restore a Vélothèque dump. By default into a scratch database (a restore *test*), then
# compare row counts with the live database. Replacing the live database needs --live.
#
#   ops/restore.sh path/to/velotheque-2026-10-15T0330Z.dump
#   ops/restore.sh --from-drive velotheque-2026-10-15T0330Z.dump     (as root on the Pi)
#   ops/restore.sh --latest-from-drive
#   ops/restore.sh --live path/to/dump                               (asks for confirmation)
#   ops/restore.sh --drop-scratch
set -euo pipefail

VELOTHEQUE_DIR="${VELOTHEQUE_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
if [ -f /etc/velotheque/backup.env ]; then
  set -a; . /etc/velotheque/backup.env; set +a
fi
TARGET="velotheque_restore_test"
LIVE=0
SOURCE=""
FROM_DRIVE=""
DROP_SCRATCH=0

while [ $# -gt 0 ]; do
  case "$1" in
    --live) LIVE=1 ;;
    --target) TARGET="$2"; shift ;;
    --from-drive) FROM_DRIVE="$2"; shift ;;
    --latest-from-drive) FROM_DRIVE="latest" ;;
    --drop-scratch) DROP_SCRATCH=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) SOURCE="$1" ;;
  esac
  shift
done

compose() { docker compose --project-directory "$VELOTHEQUE_DIR" "$@"; }
psql_db() { compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -qAt -U "$POSTGRES_USER" -d "$1"' -- "$1"; }
LIVE_DB="$(compose exec -T db sh -c 'printf %s "$POSTGRES_DB"')"

if [ "$DROP_SCRATCH" = 1 ]; then
  [ "$TARGET" != "$LIVE_DB" ] || { echo "refusing to drop the live database" >&2; exit 2; }
  echo "DROP DATABASE IF EXISTS \"$TARGET\" WITH (FORCE);" | psql_db postgres
  echo "dropped $TARGET"
  exit 0
fi

if [ -n "$FROM_DRIVE" ]; then
  : "${BACKUP_USER:=velobackup}" "${PROTON_DRIVE_BIN:=proton-drive}" "${REMOTE_PARENT:=/my-files}" "${REMOTE_FOLDER:=Velotheque}"
  export VELOTHEQUE_DATA_DIR PROTON_DRIVE_VERSION PROTON_DRIVE_BIN REMOTE_PARENT REMOTE_FOLDER
  export PROTON_DRIVE_CREDENTIALS_STORE="${PROTON_DRIVE_CREDENTIALS_STORE:-pass}"
  helper=/usr/local/lib/velotheque-backup/proton_backup.py
  [ -f "$helper" ] || helper="$VELOTHEQUE_DIR/ops/proton_backup.py"
  # The Proton session belongs to the backup user; run the CLI as that user.
  as_backup_user() {
    if [ "$(id -un)" = "$BACKUP_USER" ]; then "$@"; else
      sudo -u "$BACKUP_USER" -H --preserve-env=VELOTHEQUE_DATA_DIR,PROTON_DRIVE_VERSION,PROTON_DRIVE_BIN,PROTON_DRIVE_CREDENTIALS_STORE,REMOTE_PARENT,REMOTE_FOLDER "$@"
    fi
  }
  remote="$REMOTE_PARENT/$REMOTE_FOLDER/db"
  if [ "$FROM_DRIVE" = "latest" ]; then
    FROM_DRIVE="$(as_backup_user python3 "$helper" latest-remote-dump)"
  fi
  workdir="$(as_backup_user mktemp -d)"
  trap 'as_backup_user rm -rf "$workdir"' EXIT
  as_backup_user "$PROTON_DRIVE_BIN" filesystem download --json --file-conflict-strategy remove \
    --folder-conflict-strategy merge "$remote/$FROM_DRIVE" "$workdir" > /dev/null
  SOURCE="$workdir/$FROM_DRIVE"
  echo "downloaded $FROM_DRIVE"
fi

[ -n "$SOURCE" ] && [ -f "$SOURCE" ] || { echo "usage: $0 [--live] DUMP_FILE | --from-drive NAME | --latest-from-drive" >&2; exit 2; }
compose exec -T db pg_restore --list < "$SOURCE" > /dev/null || { echo "$SOURCE is not a readable pg_dump archive" >&2; exit 1; }

if [ "$LIVE" = 1 ]; then
  TARGET="$LIVE_DB"
  echo "This REPLACES the live database '$LIVE_DB' with $(basename "$SOURCE")."
  read -r -p "Type the database name to continue: " answer
  [ "$answer" = "$LIVE_DB" ] || { echo "aborted"; exit 1; }
  compose stop app
elif [ "$TARGET" = "$LIVE_DB" ]; then
  echo "refusing to use the live database as a scratch target; use --live" >&2
  exit 2
fi

psql_db postgres <<SQL
DROP DATABASE IF EXISTS "$TARGET" WITH (FORCE);
CREATE DATABASE "$TARGET";
SQL
compose exec -T db sh -c 'pg_restore --exit-on-error --no-owner -U "$POSTGRES_USER" -d "$1"' -- "$TARGET" < "$SOURCE"

if [ "$LIVE" = 1 ]; then
  compose start app
  echo "live database restored; the app was restarted"
  echo "regions aren't in the dump: run  docker compose exec app velotheque regions reload"
  exit 0
fi

COUNTS="SELECT string_agg(t || '=' || n, ' ' ORDER BY t) FROM (
  SELECT 'activity' t, count(*) n FROM activity UNION ALL
  SELECT 'activity_track', count(*) FROM activity_track UNION ALL
  SELECT 'activity_photo', count(*) FROM activity_photo UNION ALL
  SELECT 'bike', count(*) FROM bike UNION ALL
  SELECT 'saved_view', count(*) FROM saved_view UNION ALL
  SELECT 'raw_strava_activity', count(*) FROM raw_strava_activity UNION ALL
  SELECT 'raw_export_activity', count(*) FROM raw_export_activity UNION ALL
  SELECT 'alembic', (SELECT version_num FROM alembic_version)::text::int) x;"
restored="$(echo "$COUNTS" | psql_db "$TARGET")"
live="$(echo "$COUNTS" | psql_db "$LIVE_DB")"
echo "restored ($TARGET): $restored"
echo "live     ($LIVE_DB): $live"
if [ "$restored" = "$live" ]; then
  echo "RESTORE TEST OK: row counts match the live database"
else
  echo "RESTORE TEST DIFFERS: expected if the live database changed since the dump; check the numbers"
fi
echo "drop the scratch copy with: ops/restore.sh --drop-scratch"
