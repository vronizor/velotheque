# Vélothèque — working notes for Claude

- **What to build**: `velotheque-spec.md` (milestones, schema, VERIFY items). **Where we are**: `TODO.md` — the running checklist; update it as you work. **How it runs**: `README.md`.
- All six milestones are done (4, components, was built before the SSD move; the data is still on the Pi's SD card). Plan new features before coding them; each change must leave the app usable.
- Where the spec says **VERIFY**, check against current docs or real data and report what you found (record it in TODO.md).

## Dev loop (Mac)
```sh
docker compose up -d db                 # PostGIS (our image); `up -d --build app` for the full stack on :8000
uv run pytest                           # creates/migrates its own velotheque_test database
uv run ruff check && uv run ruff format --check
```
- UI checks: the Chrome extension is usually not connected; drive the app headlessly with Playwright from the scratchpad (`uv run --no-project --with playwright …`, SwiftShader flags) and look at screenshots.
- The local Docker stack holds a copy of the real data (export imported, Strava connected) — fine for realistic testing.

## Architecture rules
- Data layers: **raw** (append-only, `store_raw` skips identical payloads) → **normalized** (`normalize.py`, rebuilt from raw) → **derived** (`derive.py`, `velotheque rebuild-derived`) → **curated** (hand-entered, irreplaceable, backed up).
- Migrations are hand-written in `src/velotheque/migrations/versions/`; `tables.py` must match (drift test `tests/test_migrations.py`).
- Filters: register one `Field` in `filters/fields.py` (param, entities, parse, where, optional facet). OR within a field, AND across fields; facet counts exclude their own field.
- Backups run on the host, outside this repo (the Pi's own ops repo): `pg_dump` of the `db` service and new files under `photos/`. Nothing in the app reports backups. `scripts/restore-db.sh` restores a dump.
- Conventions: polars (never pandas), few dependencies, minimal comments, SQLAlchemy Core.

## Public repository
The GitHub repo is **public**. Never commit hostnames, tailnet names, IPs, usernames, Strava IDs or other personal data (use placeholders like `mypi.my-tailnet.ts.net`). Scan staged files before every commit.

## Deployment
The Pi deploys release tags on its own: a push to `main` deploys nothing; a new `vX.Y` tag on main is built, checked and rolled back if it fails, within 5 minutes. Tag only when the owner asks for a release. Don't tell the owner to `git pull` on the Pi; by hand it is `deploy velo [REF]`. Migrations run when the app container starts; a release that adds one under `src/velotheque/migrations/versions/` is held, not deployed automatically: the owner runs `deploy velo` when he can watch it. Give the owner exact commands plus what to expect, and ask for the output.
