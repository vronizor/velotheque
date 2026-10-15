#!/bin/bash
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v ro_password="$VELOTHEQUE_RO_PASSWORD" <<'SQL'
CREATE ROLE velotheque_ro LOGIN PASSWORD :'ro_password';
GRANT CONNECT ON DATABASE :"DBNAME" TO velotheque_ro;
SQL
