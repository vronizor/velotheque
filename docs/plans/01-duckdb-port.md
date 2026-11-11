# Plan 1: port Vélothèque from Postgres/PostGIS to DuckDB

Status: proposed (2026-11-11). Must be finished and running on the Pi before
[Plan 2](02-desktop-app.md) (the desktop app) starts.

## Why

- Vélothèque has one user and one writer. The Postgres server (its own container, its own role,
  its own backup format) is the main thing standing between the app and "one process, one file".
- The workload is mostly analytics (filters, facets, heatmap, area select) plus small writes
  (components, services, saved views, syncs started by hand). DuckDB suits that, and it hands
  data to polars as Arrow with almost no copying.
- One backend everywhere: no Postgres and DuckDB versions of every spatial query to keep in sync.

What we give up, and how each loss is handled:

| Postgres feature in use | DuckDB replacement |
|---|---|
| Several processes can open the DB at once (app, CLI, backup scripts, SQL client) | Only one process opens the DB read-write. See *Single-process rules* below |
| Read-only SQL role over Tailscale | Query the nightly Parquet snapshot with DuckDB or polars instead (see Backup) |
| `geography` casts, `ST_Segmentize`, `ST_Subdivide`, `ST_ClipByBox2D`, `ST_Expand`, `ST_UnaryUnion`, `ST_LineFromEncodedPolyline`, `&&`, `Box2D` | See *Spatial* below |
| tsvector + `vt_unaccent` + pg_trgm `word_similarity` | Precomputed `search_text` column, rebuilt on every sync. See *Search* below |
| Live full-text index | Not needed: `search_text` is rewritten whenever `normalize` runs, which every sync does |

## Spatial extension check (2026-11-11)

- DuckDB's v2.0.0 build config (`.github/config/extensions/spatial.cmake`) excludes spatial only
  for WASM, so it is built for `linux_arm64`.
- The docs say official extensions ship for `linux_arm64` and need glibc ≥ 2.28. The app image
  (`python:3.12-slim-bookworm`) has glibc 2.36.
- `duckdb==2.0.0` has a `manylinux_2_28_aarch64` wheel for Python 3.12.
- **Not verified yet: loading spatial on the Pi itself.** The sandbox couldn't download the
  extension binary. Run this on the Pi; it should print `POINT (1 2)`:
  ```sh
  uname -m   # must be aarch64
  docker run --rm python:3.12-slim-bookworm sh -c "pip install -q duckdb==2.0.0 && python -c \"import duckdb; c=duckdb.connect(); c.sql('INSTALL spatial; LOAD spatial; INSTALL fts'); print(c.sql('SELECT ST_AsText(ST_Point(1, 2))').fetchone()[0])\""
  ```
- Bake the extensions into the image at build time (`INSTALL spatial` in the Dockerfile, with
  `extension_directory` set to a path inside the image). Then the app never downloads anything at
  runtime, and an extension version can't change underneath a running install.

## Single-process rules

DuckDB lets one process hold the database file read-write. Inside that process, many
connections and threads are fine (optimistic MVCC; concurrent writes to the *same rows* raise a
conflict error).

- The app already runs one uvicorn worker, and sync runs on an in-process `ThreadPoolExecutor`.
  Keep both that way, and document "never add `--workers`".
- **CLI commands are separate processes.** `docker compose exec app velotheque …` fails while the
  app runs (the file is locked). Rule: maintenance commands run with the app stopped, through a
  wrapper:
  ```sh
  docker compose stop app && docker compose run --rm app velotheque regions load FRA; docker compose start app
  ```
  The CLI catches DuckDB's lock error and prints exactly that hint.
- Commands used often get a button instead: *Sync now* exists already; add *Import export zip*
  (path under `/data/imports`) and *Rebuild derived* to Settings. Then the CLI is only for rare
  jobs (regions, library imports, restore).
- `sync_job`'s partial unique index (only one `running` job) has no DuckDB equivalent: no partial
  indexes. Replace it with a `threading.Lock` in `web/jobs.py`; with one process, that lock is
  the whole truth. `mark_interrupted` at startup stays.

## Backups (systemd units + Proton Drive)

The systemd **units stay as they are** (timer at 03:30, oneshot service, `velobackup` user).
Three scripts change, because each one assumes a second process can reach the DB:

| Script | Today | After the port |
|---|---|---|
| `ops/dump-db.sh` (ExecStartPre, root) | `pg_dump` inside the `db` container | `compose stop app` → `compose run --rm app velotheque snapshot /data/backups/<ts>` → `compose start app`. Then tar the snapshot dir into `$STATE_DIR/dumps/velotheque-<ts>.tar` |
| `ops/record-status.sh` (ExecStopPost, root) | `compose exec app velotheque backup-status` (writes the DB) | Appends a line to `$VELOTHEQUE_DATA_DIR/backup-status.jsonl`. No DB write, so no lock, and the multi-hour upload never collides with the app |
| `ops/restore.sh` | `pg_restore` into a scratch DB or the live one | Restore test: load the tar into a scratch `.duckdb` file and compare row counts with the latest snapshot. Live restore: stop app, load into a new file, swap files, start app |

- **Snapshot format:** one Parquet file per table plus `schema.sql`, written by an in-process
  `velotheque snapshot` command. Parquet is readable by any DuckDB or polars version, which
  matters because an older DuckDB can't open a newer DuckDB file. Region tables are left out, as
  today (`region_country` stays, so `regions reload` knows what to fetch).
- **Downtime:** the app is stopped for the seconds the snapshot takes, at 03:30. A sync running
  at that moment gets marked `interrupted` at restart, as it is today after any restart.
- **Read-only SQL replacement:** the latest snapshot dir on the Pi (and on Proton Drive) can be
  queried directly: `duckdb -c "SELECT … FROM '/srv/…/backups/latest/activity.parquet'"`.
- The app reads backup status from `backup-status.jsonl` instead of the `backup_status` table.
  The header indicator and the Settings history keep working; the table is dropped.
- `ops/proton_backup.py`: change the `DUMP_NAME` regex and the `latest_dump` glob from `.dump`
  to `.tar`. Retention logic is unchanged. Photos are files on disk and are unaffected.
- After deploying: `sudo ops/install-backup.sh`, then `sudo systemctl start velotheque-backup`
  and `journalctl -u velotheque-backup -n 50`.

The alternative was an authenticated admin HTTP endpoint that snapshots without stopping the app.
It means no downtime, but adds an auth surface reachable over the tailnet (`tailscale serve`
proxies from 127.0.0.1, so a localhost check isn't enough). That isn't worth it for one user at
03:30.

## Schema and migrations

DuckDB differences that hit `tables.py` directly:

- **No `GENERATED … AS IDENTITY`** (18 columns): use `CREATE SEQUENCE` + `DEFAULT nextval(...)`.
- **No `ON DELETE CASCADE` / `SET NULL`** (21 foreign keys): delete children explicitly in the
  few delete paths (activity, route, component, companion, bike, region country). Better still,
  drop foreign keys to derived tables entirely, since `rebuild-derived` recreates those.
- **Generated columns are virtual only**, so `search_tsv` can't be stored. It goes away anyway
  (see Search).
- **No partial indexes** (`sync_job`, see above). **No GIN/GiST:** ART indexes for keys, an
  R-tree (`CREATE INDEX … USING RTREE`) on geometry columns that are filtered spatially.
- `JSONB` → `JSON`; `ARRAY` → `LIST`; `INTERVAL`, `LATERAL`, `DISTINCT ON`, `RETURNING`,
  `ON CONFLICT`, `WITH RECURSIVE`, `AS MATERIALIZED` CTEs all exist.
- **VERIFY:** `= ANY(:ids)` with a list parameter (fallback: `list_contains(:ids, x)`);
  `TRUNCATE a, b, c` (fallback: one `DELETE FROM` per table); DuckDB's over-eager unique/FK
  checks when a row is updated and re-inserted in the same transaction.
- **Migrations:** the 0001–0011 history is Postgres DDL. Start the DuckDB branch from a new
  `0001_baseline` that matches today's schema, and keep the drift test (`tests/test_migrations.py`).
  Alembic needs a small `DuckDBImpl` registration. **VERIFY** how well duckdb_engine and Alembic
  handle `ALTER TABLE` before relying on autogenerate; migrations here are hand-written anyway.
- **SQLAlchemy:** `duckdb_engine`'s dialect subclasses the Postgres one, so most Core code
  (including `postgresql.insert().on_conflict_do_update`) should compile unchanged. **VERIFY**
  first. Replace `geoalchemy2` (no DuckDB support) with a ~30-line `TypeDecorator` that binds WKB
  through `ST_GeomFromWKB` and reads with `ST_AsWKB`.
- Settings: `memory_limit` (start at 1 GB on the Pi), `threads` (2–4), and
  `extension_directory` in the image.

## Spatial

Functions used today that DuckDB spatial has, unchanged: `ST_AsGeoJSON`, `ST_GeomFromText`,
`ST_Multi`, `ST_Transform`, `ST_Length`, `ST_SimplifyPreserveTopology`, `ST_Intersection`,
`ST_Intersects`, `ST_Buffer`, `ST_Dump`, `ST_Collect`, `ST_MakeValid`, `ST_X/Y/XMin/…`,
`ST_Centroid`, `ST_Union`, `ST_MakeEnvelope`, `ST_NPoints`, `ST_Extent`, `ST_Simplify`,
`ST_IsValid`, `ST_CollectionExtract`, `ST_NumGeometries`, `ST_AsText`.

Missing ones and their replacements:

| Postgres | Where | Replacement |
|---|---|---|
| `::geography` lengths | derive.py, routes.py | `ST_Length_Spheroid`. **Gotcha:** DuckDB's spheroid functions expect lat/lon axis order, so wrap with `ST_FlipCoordinates`. Check a known route's length against today's value |
| `ST_Segmentize` | derive.py `_buffer` | Do the buffer in Python with shapely (`segmentize`, `buffer`, pyproj for UTM). Or test whether a plain UTM buffer of the 5 m `detail` line changes overlap results at all (rides are already dense) |
| `ST_ClipByBox2D` | derive.py | Plain `ST_Intersection` with `ST_MakeEnvelope`. It was only an optimization |
| `&&`, `ST_Expand`, `Box2D` | derive.py | `ST_Intersects_Extent`, plus an envelope built from `ST_XMin - d`, … |
| `ST_UnaryUnion` | route length | `ST_Union_Agg` over `ST_Dump` parts. **VERIFY** it dissolves exactly repeated out-and-back segments the same way |
| `ST_Subdivide` | region_part | Option A: no subdivision, R-tree on `region.geometry`, then time region tagging on a full re-derive. Option B: a ~20-line quadtree clip in Python (shapely `clip_by_rect`) |
| `ST_LineFromEncodedPolyline` | Strava summary polylines | Decode in Python (the polyline algorithm is ~15 lines, no dependency) |
| `GeometryType()` | derive.py | `ST_GeometryType()` (returns `LINESTRING`) |

Region loading: `regions.py` already parses the GeoPackage with stdlib sqlite3. Only the
psycopg `COPY` changes, into a polars frame inserted with `ST_GeomFromWKB`.

Route–ride overlap (`OVERLAPS`/`COVERAGE`) is the riskiest query. Port it last, behind a parity
test: same routes, same rides, compare `overlap_m` and `coverage_fraction` per pair between the
Postgres and DuckDB builds and accept small float differences.

## Search

Today: an accent-insensitive prefix match on every word of name + description (no stemming,
because titles mix French, Spanish, Catalan and English), OR a typo-tolerant `word_similarity`
on the name.

DuckDB's `fts` extension doesn't fit: it matches whole stemmed words with BM25 ranking, and has
no prefix matching. Instead:

- `normalize` writes `search_text = ' ' || strip_accents(lower(name || ' ' || description))`.
  It runs on every sync, so the "index" is rebuilt on sync with no extra step.
- Prefix match per word: `search_text LIKE '% ' || word || '%'`, ANDed across words. That's a
  full scan of a few thousand rows, which takes milliseconds.
- Fuzzy name match: no trigram `word_similarity`. Approximate it with the best
  `jaro_winkler_similarity` between the query and each word of the name, then retune
  `FUZZY_THRESHOLD` against the existing tests in `test_filters.py` / `test_web.py`.

## Data migration (Pi)

Only raw and curated data are irreplaceable; everything else is rebuilt.

1. Add `velotheque migrate-from-postgres` (one-off). It reads every non-derived table through
   psycopg (geometries as WKB), writes them into a fresh DuckDB file through polars, then runs
   `normalize`, `rebuild-derived` and `regions reload`.
2. Run it on the Pi with both stacks up, the DuckDB file next to the Postgres data.
3. Parity check: row counts per table, ride count and total distance per year, the same three
   saved views and two area selects returning the same IDs, component hours per component, and
   overlap fractions as above.
4. Switch `compose.yaml` to the DuckDB build. **Keep the Postgres volume and the last `.dump`
   untouched for a month**, then remove the `db` service, `docker/db/` and the role script.

## Work order (each step leaves the app usable)

1. Pi check above; pin `duckdb` in `pyproject.toml`; bake extensions into the image.
2. DB layer: engine and settings, geometry `TypeDecorator`, baseline migration, sequences,
   explicit cascades, lock replacing the partial index. Tests run on a temp DuckDB file
   (conftest), so CI no longer needs Postgres.
3. Port queries file by file, simplest first: `queries.py`, `filters/` (search last),
   `components.py`, `companions.py`, `photos.py`, `normalize.py`, `routes.py`, `regions.py`,
   `derive.py` (overlap last).
4. CLI lock-error hint; Settings buttons for import-export and rebuild-derived.
5. Backup: `velotheque snapshot`, the three ops scripts, `backup-status.jsonl`, restore test.
6. `migrate-from-postgres`, parity checks on the Pi, switch over.
7. README: stack, CLI section (stop/run/start), backup and restore, read-only SQL → Parquet
   snapshot. TODO.md: VERIFY results.

Rough size: about 1,500 of the ~7,400 lines in `src/` are touched; `derive.py` and the
search/fuzzy filter are the hard parts. Two to three focused weekends with Claude Code, most of
it parity checking.
