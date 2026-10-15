# Vélothèque — TODO

Plan: Milestone 1 (Skeleton + sync). Spec: `velotheque-spec.md`.
Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked

## Blockers / waiting on the owner
- [x] Docker Desktop installed on the Mac
- [!] Git remote URL — wasn't in the message; needed before the first push
- [x] Pi access: skipped by choice. Test on local Docker (arm64, same as the Pi); owner git-pulls on the Pi
- [ ] Browser check of the UI (Claude-in-Chrome extension not connected; manual check by the owner)
- [ ] Put `STRAVA_CLIENT_SECRET` in `.env` (never commit it)
- [ ] Proton Drive CLI on the Pi (M3). Mac build is at `~/Downloads/proton-drive`; docs: https://proton.me/support/drive-cli
  - M3 VERIFY: Linux arm64 build exists? (docs say macOS/Windows/Linux, arch unspecified)
  - M3 VERIFY: delete/trash command (not in docs; check `--help`) → else rotate fixed filenames
  - M3 VERIFY: headless login / session file location (docs only show browser `auth login`)
- [ ] Click "Upgrade" in the Strava API dashboard (read limit 100/1,000 → 200/2,000)
- [ ] Note: the dashboard's own access token only has `scope: read`. The app gets its own token via the OAuth flow, with `read,activity:read_all,profile:read_all`.

## VERIFY log
- [~] Callback domain = Tailscale MagicDNS host: the Pi's MagicDNS host entered in the Strava app settings. Confirm at the first real Connect.
- [x] Rate-limit headers: `X-RateLimit-*` / `X-ReadRateLimit-*`, values `"15min,daily"`
- [x] PostGIS arm64: `postgresql-18-postgis-3` 3.6.x in PGDG bookworm-pgdg
- [x] 2026 Strava API changes: nothing blocking. API base URL is configurable, Bearer header always sent.

## M2 notes (gathered early)
- Bulk export at `data/imports/` (gitignored): ~663 activity files, 642 media, 78 routes
- `activities.csv` VERIFY: columns include `Activity ID, Activity Date, Activity Name, Activity Type, Activity Description, Elapsed Time, Distance, …, Activity Gear, Filename, …` and **duplicate headers** (`Elapsed Time`, `Distance` appear twice: summary vs detailed units) → polars must read with deduplicated names. Media column still to locate.
- ~660 activities → full API backfill ≈ 1,300 read calls ≈ 1–2 days at 1,000/day

## M1 — Skeleton + sync
### 0. Scaffold
- [x] git init, .gitignore, TODO.md
- [x] pyproject (uv), ruff, pytest config
- [x] config.py (pydantic-settings), .env.example
- [x] logo → static

### 1. Database
- [x] docker/db/Dockerfile (postgres:18-bookworm + postgresql-18-postgis-3)
- [x] docker/db/initdb read-only role script
- [x] docker/app/Dockerfile, compose.yaml (app image builds on Mac arm64)
- [x] Alembic setup + 0001_initial (raw, operational, normalized, derived tables, grants)
- [x] Verify: compose up, migrate, postgis_full_version() = 3.6.4 on PG 18; ro role denied on strava_auth; migration drift test passes

### 2. Strava auth
- [x] client.py: Bearer auth, token refresh with rotation, configurable base URL
- [x] ratelimit.py: header parsing, budget guard
- [x] OAuth routes: /strava/connect, /strava/callback (state cookie, scope check)
- [x] Settings page: connection status + Connect button
- [ ] Verify: real Connect works end to end (needs STRAVA_CLIENT_SECRET)
- [ ] Deploy to Pi: clone, .env, compose up, Caddy site block
- [ ] First real sync on the Pi (not on the Mac, to keep the daily read budget)

### 3. Sync
- [x] Athlete + gear fetch → raw
- [x] Activity list (after = max(start_at) − 14 d, per_page=200) → raw summaries
- [x] Pending queue (signature diff / missing streams), newest first
- [x] Detail + streams fetch → raw
- [x] sync_job lifecycle, single-thread executor, interrupted on startup
- [x] Header "Sync now" + HTMX job polling; sync history on Settings
- [x] CLI `velotheque sync`

### 4. Normalize + derive
- [x] bike from athlete/gear
- [x] activity from latest summary/detail (detail wins), tz parsing
- [x] activity_track from streams (gap split) or polyline fallback (detail map.polyline, else summary_polyline)
- [x] activity_track_simplified (overview 25 m, detail 5 m)
- [x] CLI `normalize --all`, `rebuild-derived`

### 5. Filters + Explore
- [x] Filter registry (Field, FilterState, build_where with exclude)
- [x] Fields: after, before, type, bike, dist_min/max, elev_min/max, dur_min/max (+ tests)
- [x] Explore page: sidebar, map (MapLibre, OpenFreeMap), sortable list, totals
- [x] rides.geojson built in SQL, gzip
- [x] List ↔ map highlight
- [x] Vendor htmx + maplibre (scripts/vendor.sh)

### 6. Ride page
- [x] Track map (detail level), stats, description, bike
- [x] "Refresh from Strava" button

### 7. Docs + verification
- [x] Web smoke tests (TestClient): explore page/partial, geojson filters, ride page, OAuth state check (38 tests total)
- [x] README: setup, env vars, Strava connection (both callback flows), read-only role, Caddy snippet
- [x] Tests: filter builder, sync (MockTransport), normalize, rebuild-derived idempotency, rate limiter (32 passing)
- [~] scripts/seed_synthetic.py ✔; Mac: 5k rides → 3.7 MB gz / 0.58 s unfiltered, <0.15 s filtered. Pi measurement pending.
- [x] ruff check + format clean
- [ ] Push to remote
