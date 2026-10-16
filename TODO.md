# Vélothèque — TODO

Plan: Milestone 5 (Routes); M4 deferred until the SSD move. Spec: `velotheque-spec.md`.
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

## Pi deployment notes
- No SSD mounted yet: data on the SD card at /srv/velotheque (not under /mnt/ssd, so a later mount can't hide it). 15 GB free. Move to the SSD (rsync -aHAX, then change VELOTHEQUE_DATA_DIR in .env and backup.env) before Milestone 4's curated data exists.
- No Caddy on the Pi (the spec assumed one): HTTPS + names via Tailscale Services (`tailscale serve --service=svc:<name> --https=443 127.0.0.1:<port>`; Pi tagged tag:server; Funnel left off). BASE_URL and the Strava callback domain use the service name.
- /etc/velotheque/backup.env on the Pi: VELOTHEQUE_DATA_DIR=/srv/velotheque until the SSD move
- Port 8000 (and 8001, 8080, 80, 53, 3000) are used by other services on the Pi: APP_PORT=8010 there.

## Pi deployment checklist (2026-10-16)
- [x] Stack up on the Pi (APP_PORT 8010, data on SD at /srv/velotheque), migrations 0001–0004, PostGIS 3.6.4
- [x] HTTPS + name via Tailscale Services
- [x] Export imported, Strava connected + synced on the Pi
- [x] Perf on the Pi under 1 s
- [x] Backup: first run (531 photos), restore test from Drive OK, nightly timer, reboot test OK
- [ ] Move data to the SSD before Milestone 4 (see Pi deployment notes)

## M5 — Routes (started 2026-10-16; M4 deferred until the SSD is in)
Decisions (owner, 2026-10-16): GPX only (the library's .geojson files are point-for-point copies of the .gpx); stage files of a race can be merged into one route; Strava export routes imported with source=strava so they can be filtered out; Strava-style ride page work comes right after M5. No status (the library is inspiration, not a to-do list): free-form tags instead (filter + counts; `mtb` auto-set from Strava GPX type); source = strava | gpx_studio | file; "ridden" is shown from coverage, never set by hand.
- [x] VERIFY real data: export has 77 route GPX (55 cycling, 12 MTB, 10 running → skipped); library has 4 files with `<rte>`/`<rtept>` (not `<trk>`), combined race files with 5–21 tracks (distance must not bridge them: 5,189 vs 2,184 km), sparse planned routes (no gap splitting), up to 100k points
- [x] Prototype overlap on real rides: ST_Intersection dissolves a route's exactly repeated out-and-back parts, and UTM round trips stop pieces from merging (coverage > 100%) → buffer built in UTM per ride, intersection in route coordinates, fractions over the route's dissolved length, pieces < 100 m (road crossings) dropped
- [x] Elevation gain from GPX: 10 m hysteresis, calibrated on 330 real rides vs Strava (median ratio 0.997, p10–p90 0.69–1.10)
- [x] Migration 0005: raw_gpx_file (bytes in the DB → in the nightly dump), route (+ search, tags text[]), route_file (merged stages), route_simplified (overview 50 m: routes run to 1000s of km), route_ride_overlap (+ covered pieces), route_coverage
- [x] Route parser (trk/trkseg/rte; TCX/FIT as one piece; multi-track files named by metadata or file name), normalize routes from raw
- [x] CLI `routes import FILE… [--merge] [--tag] [--source] [--name]`, `routes import-export ZIP`
- [x] Derived: overlaps + coverage, updated on route import and on ride sync, in rebuild-derived. Coverage from hair-thin buffers around each ride's covered pieces (polygon union of ride buffers took 37 s of 43); rides segmentized before buffering so long straight segments match
- [x] Filters: tag, source, overlaps_route (`id` = ≥30%, `id:pct`), coverage_min/max; q/dist/elev/bbox/area for routes
- [x] Explore: Rides | Routes | Both, dashed route layer, route list + totals + tag/source facets; toggle + reset refreshed with results
- [x] Route page: map with ridden parts, coverage, overlapping rides, edit name/tags/notes, GPX download, delete
- [x] Import form (several files, optional merge)
- [x] Visible "Reset filters" button (owner request; keeps the Rides/Routes toggle)
- [x] Ride page: routes it rides
- [x] Tests: parser, elevation, idempotent import + merge order, export routes, overlap on synthetic geometries (half, offset, crossing, out-and-back, merged coverage, sync update, rebuild), filters + facets, web (95 total)
- [x] Real data locally: 36 library routes (5 races merged from stages) + 52 Strava routes (15 byte-identical duplicates skipped, 10 running skipped); import ~0.5 s/route, rebuild 18 s on the Mac; routes GeoJSON 0.62 MB gz; headless check of Explore (routes/both/tag filter/selection), route pages, import form, ride page, phone
- [x] README
- [x] Deploy to the Pi + import there (owner, 2026-10-16): library via `import-library --separate personal` = 40 routes, export = 52 routes / 25 skipped (as on the Mac). 4 races came in twice: the library has both a single `<race>.gpx` and a `<race>/` stage folder with identical points (same km, same point count) → owner deletes one of each
- [x] `routes import-library DIR` (one route per top-level file, subfolders merged and named after the folder, .geojson ignored), so the Pi import is one command
- [x] Static CSS/JS URLs carry a content hash (`?v=`) so a deploy isn't hidden by the browser cache; ride page "On routes" marker inline with the heading (owner: it sat on its own line)
- [x] Owner feedback (2026-10-16): source is free text (migration 0006 drops the check, renames to `Strava` / `gpx.studio`; reuses an existing spelling case-insensitively; suggestions in a datalist), editable on the route page, badge on every route; bulk edit from the Explore list (tick routes → set source, add/remove tags); "On routes" on the ride page collapsible (closed by default)
- Notes: the Strava export has near-duplicate routes (same name, slightly different files) → both kept; delete one in the UI. Names come from the GPX (some are file-ish codes): editable on the route page.

## After M5: Strava-style ride page (study done 2026-10-16)
- [x] Videos inside the lightbox carousel (tiles show the first frame + ▶; ‹ › buttons, arrow keys; pauses on close)
- [x] Resizable ride and route maps (drag bar or ↑/↓, remembered per page kind); map/list splitter on Explore (`--map-share`, 15–85%), off on phones
- [x] Elevation chart + synced hover (km, altitude, grade over ±100 m), hand-written SVG, ~1000 samples embedded in the page (≈30 KB); hovering the map track moves the chart cursor. Positions come from the track points (all 341 local tracks line up with their streams; fallback interpolates along the line)
- [x] Owner request: "show the routes it rides" on the ride map (fetched on first tick from `/rides/{id}/routes.geojson`, overview lines) and "rides" toggle on the route map; both remembered
- [x] Owner request (gpx.studio-style): drag on the elevation chart to select a section → distance, gain/loss (JS port of the 10 m hysteresis), avg grade, yellow highlight on the map cut from the track at the exact distances, Zoom map / Clear / Esc
- [x] Double-click a resize bar resets it to the default size
- [x] Route pages get the elevation chart + section measuring: migration 0007 adds `route.altitude_m` (one value per vertex, filled by normalize_route); distances along the pieces without bridging gaps; `routes normalize` backfills (88 routes in 20 s on the Mac; 85 have elevation)
- [x] Clicking a ride/route table row selects it like in Explore (row tint, yellow section kept on the map and as bands on the elevation chart: profile points within 40 m of the section); hover previews others; row again / Esc / click elsewhere (map and chart excepted) deselects. ↺ Reset zoom button under +/− on ride and route maps
- [x] Ride page "On routes" is now the same table as the route page's "Rides on this route" (Name · Route km · On route · Of route), same hover highlight / click zoom; shared `initPairRows` (owner: ride and route pages should feel symmetrical)
- [x] "Rides on this route" collapsible (sections remember open/closed); hovering a row highlights that ride's covered part of the route (fetched once from `/routes/{id}/rides/{ride}.geojson`) and its line, clicking the row zooms to it
- [x] Dropped (owner, not needed): link Strava routes to their Strava page. The export has no route id (`routes.csv` = name + `routes/N.gpx`, empty `<link>` in the GPX) and sync doesn't touch routes, so this needs `GET /athletes/{id}/routes` (needs the `read_all` scope for private routes; check the current token's scope) and a match on name + distance. Owner: not needed (2026-10-16)
- [ ] Descriptions show Strava mentions raw (`[strava://athletes/…]`); render them as the name only
- [!] Photo markers: none of the 517 photos has EXIF GPS or capture time (Strava strips them; media.csv has only filename + caption). Also a spec non-goal. Only lead: undocumented API photo endpoint

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
- [x] scripts/seed_synthetic.py ✔; Mac: 5k rides → 3.7 MB gz / 0.58 s unfiltered, <0.15 s filtered. Pi (real 390 rides): page 0.04 s, full map 0.84 MB gz in 0.23 s ✔ (<1 s)
- [x] ruff check + format clean
- [x] Push to remote (github.com/vronizor/velotheque, public: keep personal hostnames/IPs out of tracked files)
