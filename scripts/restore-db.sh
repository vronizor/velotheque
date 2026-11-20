#!/usr/bin/env bash
# Restore a pg_dump of Vélothèque (custom format, e.g. from the host's backup). By default into
# a scratch database (a restore *test*), then compare row counts with the live database.
# Replacing the live database needs --live.
#
#   scripts/restore-db.sh path/to/velotheque.dump
#   scripts/restore-db.sh --live path/to/velotheque.dump      (asks for confirmation)
#   scripts/restore-db.sh --drop-scratch
set -euo pipefail

VELOTHEQUE_DIR="${VELOTHEQUE_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
TARGET="velotheque_restore_test"
LIVE=0
SOURCE=""
DROP_SCRATCH=0

while [ $# -gt 0 ]; do
  case "$1" in
    --live) LIVE=1 ;;
    --target) TARGET="$2"; shift ;;
    --drop-scratch) DROP_SCRATCH=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
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

[ -n "$SOURCE" ] && [ -f "$SOURCE" ] || { echo "usage: $0 [--live] DUMP_FILE | --drop-scratch" >&2; exit 2; }
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
echo "drop the scratch copy with: scripts/restore-db.sh --drop-scratch"
