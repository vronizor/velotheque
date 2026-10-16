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
- Host-side backup code in `ops/` is stdlib-only; everything Proton-specific lives in `ops/proton_backup.py`.
- Conventions: polars (never pandas), few dependencies, minimal comments, SQLAlchemy Core.

## Public repository
The GitHub repo is **public**. Never commit hostnames, tailnet names, IPs, usernames, Strava IDs or other personal data (use placeholders like `mypi.my-tailnet.ts.net`). Scan staged files before every commit.

## Deployment
The owner deploys by `git pull` on the Pi; migrations run when the app container starts. After changes under `ops/`, the owner re-runs `sudo ops/install-backup.sh`. Give the owner exact commands plus what to expect, and ask for the output.
