# Vélothèque — TODO

Plan: Milestone 1 (Skeleton + sync). Spec: `velotheque-spec.md`.
Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked

## Blockers / waiting on the owner
- [x] Docker Desktop installed on the Mac
- [!] Git remote URL — wasn't in the message; needed before the first push
- [x] Pi access: skipped by choice. Test on local Docker (arm64, same as the Pi); owner git-pulls on the Pi
- [x] Browser check of the UI: done by the owner, all working
- [ ] Put `STRAVA_CLIENT_SECRET` in `.env` (never commit it)
- [ ] Proton Drive CLI on the Pi: install linux/arm64 build + `sudo ops/install-backup.sh` + login (README → Backup). VERIFY results are in the M3 section.
- [ ] Click "Upgrade" in the Strava API dashboard (read limit 100/1,000 → 200/2,000)
- [ ] Note: the dashboard's own access token only has `scope: read`. The app gets its own token via the OAuth flow, with `read,activity:read_all,profile:read_all`.

## VERIFY log
- [~] Callback domain = Tailscale MagicDNS host: the Pi's MagicDNS host entered in the Strava app settings. Confirm at the first real Connect.
- [x] Rate-limit headers: `X-RateLimit-*` / `X-ReadRateLimit-*`, values `"15min,daily"`
- [x] PostGIS arm64: `postgresql-18-postgis-3` 3.6.x in PGDG bookworm-pgdg
- [x] 2026 Strava API changes: nothing blocking. API base URL is configurable, Bearer header always sent.

## Owner feedback (2026-10-15)
- [x] Clicking a list row (not the name) selects the ride and zooms the map to it; other rides stay drawn; click again to deselect; the name still opens the ride page
- [x] "↺ Reset map" next to the drawing tools: clears the map area + selection, zooms back to all results
- [ ] Filter by place name (Aragón, Paris, …) = region filter, spec Milestone 6 (GADM). Not built yet; search only matches ride names/descriptions

## M3 — Backup to Proton Drive
- [x] VERIFY proton-drive CLI v0.9.0 (Mac binary + docs): linux/arm64 glibc build exists; `filesystem delete` + `trash` exist → prune by name; headless = `auth login` URL opened elsewhere + PROTON_DRIVE_CREDENTIALS_STORE (keychain | pass | unsafe_file); upload strategies replace/skip/rename/create-new-revision, folders merge. Per-command --help broken in 0.9.0; list --json shape + exit codes undocumented
- [x] VERIFY live (Mac, throwaway folder, since deleted): list --json = array of nodes with name {"ok", "value"} (first parser guess was wrong → fixed + test); errors exit 1 with stderr; folder merge creates the remote tree; unchanged re-upload skipped (22 s → 4.5 s); delete works only from /trash (pruning trashes); version pin refusal; restore --latest-from-drive OK in 13 s with matching counts
- [x] Migration 0004: backup_status (+ app reads it)
- [x] `velotheque backup-status` CLI the host script calls to record success/failure
- [x] ops/dump-db.sh (root, Docker only) + ops/proton_backup.py (backup user): dump → Velotheque/db/<UTC>.dump; retention 14 daily + 8 weekly (trash)
- [x] incremental photo upload with a local manifest, batches of 200
- [x] ops/restore.sh + documented restore test into a scratch DB
- [x] systemd service + timer units (dedicated user), install notes
- [x] Header backup indicator (red if last success > 48 h); Settings backup section
- [x] Tests: retention selection, manifest diff, status recording, indicator
- [x] Local dry run: 49 MB dump → scratch DB in ~5 s, row counts match; record-status success/failure paths → indicator
- [x] README + push

## M5 notes (gathered early)
- GPX library lives on Proton Drive at `/my-files/gpx-library`: 16 .gpx + 15 .geojson twins + 6 subfolders (multi-stage events?). Decide: GPX only, or GeoJSON too? Pull it with proton-drive for testing when M5 starts.

## M2 (pulled forward): bulk export import
- [x] `velotheque import-export <zip>`: raw_export_activity (migration 0002), track files copied to DATA_DIR/strava-export/<export>/
- [x] Track parsers: FIT (incl. byte-typed big-endian fields fitdecode can't decode), GPX, TCX; gz or plain
- [x] Gear via the CSV `Bike` column → `b<id>` (exact); unmatched cycling gear listed by the CLI
- [x] Normalize merge: API wins for metadata; tracks streams > export > polyline; sync skips detail/streams for export-covered rides
- [x] Real export imported locally: 390 rides, 341 tracks (4 Rides + 45 Virtual Rides have no GPS), 7 bikes, 18,958 km; map 0.84 MB gz / 0.12 s

## M2 — History + search (remaining)
### Photos
- [x] VERIFY: no documented endpoint lists all photos of an activity (detail payload has only the primary, sizes 100/600). Took the spec's fallback: primary photo via the detail we already fetch + newer exports fill in the rest. primary.unique_id == export media file UUID (checked on a real ride) → dedupe key
- [x] Migration 0003: activity_photo, saved_view, search column (raw API photo payloads not needed: the detail payload is already raw)
- [x] Export media → DATA_DIR/photos/{activity_id}/ + ~400px thumbnails (Pillow); videos kept, no thumbnail
- [x] Sync: primary photo from the detail payload (no extra API call), downloaded from Strava's CDN
- [x] Ride page gallery (thumbnails → full size; videos inline)
### Search + facets
- [x] Migration: unaccent + pg_trgm, immutable `vt_unaccent()`, generated tsvector on activity (simple config), trigram index on name
- [x] `q` filter: prefix full-text over name/description OR fuzzy word-similarity on name
- [x] Facet counts for type, bike, photos (each excluding its own field), refreshed with results (htmx OOB)
- [x] `photos` filter (has photos)
### Map drawing
- [x] `bbox` + `area` (encoded polyline) filters on track geometry (GiST)
- [x] Vendor Terra Draw + MapLibre adapter; draw rectangle / polygon tools; "Filter to this view"; clear; show active area on the map
### Saved views
- [x] saved_view table; "Save view" (name → query string); list + delete in the sidebar
### Misc
- [x] Real data: 517 photos + 14 videos on 132 rides (440 MB); headless-browser check of filters, facets, search, rectangle/polygon/view drawing, saved views, back button, gallery + lightbox, phone layout
- [x] Phone layout: map → results → filters order on narrow screens
- [x] Settings: export import summary + rides whose gear couldn't be matched
- [x] Tests: q/unaccent/fuzzy, facets exclude own field, bbox/area, saved views, photo import
- [x] README + push

## M2 notes (gathered early)
- Bulk export at `data/imports/` (gitignored): ~663 activity files, 642 media, 78 routes
- [x] `activities.csv` VERIFY: 103 columns. Activity Date is UTC (checked against FIT timestamps). `Bike` = Strava gear id. `Filename` = activities/<upload id>.{fit.gz,gpx,tcx.gz,fit}. `Media` = `||media/a.jpg|media/b.mp4`. Columns include `Activity ID, Activity Date, Activity Name, Activity Type, Activity Description, Elapsed Time, Distance, …, Activity Gear, Filename, …` and **duplicate headers** (`Elapsed Time`, `Distance` appear twice: summary vs detailed units) → polars must read with deduplicated names.
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
- [x] Verify: real Connect works end to end (localhost redirect; scopes read,activity:read_all,profile:read_all granted)
- [ ] Deploy to Pi: clone, .env, compose up, Caddy site block
- [x] First real sync (local Docker): 663 listed, 0 detail/stream calls thanks to the export, 32 s. Progress counter now updates every 25 activities.

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
- [x] Push to remote (github.com/vronizor/velotheque, public: keep personal hostnames/IPs out of tracked files)
