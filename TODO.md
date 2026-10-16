# Vélothèque — TODO

Plan: Milestone 4 (Components); started before the SSD move (owner, 2026-10-16). Spec: `velotheque-spec.md`.
Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked

## Blockers / waiting on the owner
- [x] Docker Desktop installed on the Mac
- [x] Git remote URL (GitHub, public)
- [x] Pi access: skipped by choice. Test on local Docker (arm64, same as the Pi); owner git-pulls on the Pi
- [x] Browser check of the UI: done by the owner, all working
- [x] Put `STRAVA_CLIENT_SECRET` in `.env` (never commit it)
- [ ] Proton Drive CLI on the Pi: install linux/arm64 build + `sudo ops/install-backup.sh` + login (README → Backup). VERIFY results are in the M3 section.
- [x] Click "Upgrade" in the Strava API dashboard (read limit 100/1,000 → 200/2,000)
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

## Open in gpx.studio (owner request, 2026-10-16)
VERIFY (2026-10-16, gpx.studio source on GitHub): its "Open in" link is `https://gpx.studio/app?files=<encodeURIComponent(JSON.stringify([urls]))>` (also `ids=` for Google Drive). The app `fetch()`es each URL **in the browser** and names the file after the last URL segment → our server must send CORS (`Access-Control-Allow-Origin`), and URLs end in `<slug>.gpx`. Chrome 142+ Local Network Access: a public site fetching a local address needs a one-time permission; the LNA spec lists 100.64.0.0/10 (CGNAT, Tailscale) as **local**. No preflight or extra header needed. Headless Chrome auto-denies it (CORS error "Permission was denied"); with the permission granted the file loads.
- [x] Ride: GPX 1.1 generated from activity_track (time + ele per point, one trkseg per piece), `/rides/{id}/gpx/{slug}.gpx`; route: original files under `/routes/{id}/files/{file}/{slug}.{ext}`; both with `Access-Control-Allow-Origin: https://gpx.studio`
- [x] Buttons on ride and route pages (symmetry), Download GPX, Import a GPX shortcut on routes; links from BASE_URL
- [x] Tests: GPX round trip through our own parser, slugs, link encoding, web (CORS header, file names, original bytes)
- [x] Real check: headless Chrome opened a local ride in gpx.studio (76.76 km, elevation profile, times OK) and a 5-stage race (5 files, one per stage)
- [ ] Deploy to the Pi (owner): `git pull`, rebuild; first click → allow Chrome's local-network prompt

## M4 — Components and maintenance (2026-10-16, before the SSD move)
Decisions (owner, 2026-10-16): intervals in km, hours **or days** (calendar time since the last reset, ridden or not: sealant, brake fluid, yearly fork service); each service type counts **all / outdoor only / indoor only** rides (owner's case: an old road bike as the home-trainer bike → chain all, bike cassette and rear tyre outdoor, trainer cassette indoor); bars amber at 90%, red past 100%, plus a "Due soon" list (≥ 50%, most worn first) to know what to order. Old gym virtual rides stay without a bike (owner doesn't care); nothing backfills bikes: Strava's default gear applies to new uploads, and sync only re-reads 14 days. SSD not there yet: curated data starts on the SD card, covered by the nightly Proton backup.
Rules: usage = rides on the assignment's bike with `mounted_at <= start_at < removed_at`, after the latest reset (`start_at > performed_at`); an event of level L resets the component's service types of level ≤ L; an event without a service type is a note and resets nothing; indoor = trainer flag or VirtualRide. Computed on demand in one query (`components.USAGE`), never stored. Times typed in forms are read in `TIMEZONE` (env), else the latest ride's timezone.
- [x] Migration 0010 (curated): `component` (name, kind, brand, model, notes, retired_at), `component_assignment` (bike, mounted_at, removed_at; btree_gist exclusion `[mounted_at, removed_at)`: one bike at a time, back-to-back periods allowed), `service_type` (name, level, interval value + unit km|hours|days, rides all|outdoor|indoor), `service_event` (component, optional service type with a composite FK so it belongs to the same component, performed_at, notes)
- [x] `components.py`: add/edit, mount/move/unmount/retire, past periods (add/edit/delete), service types (copy a schedule from another component; delete only while unused), events; usage + lifetime queries
- [x] Filter `component` (rides done while it was mounted), shown in Explore when active
- [x] Components page: Due soon; per bike, mounted components with lifetime km/h and service bars + quick "Log"; bikes with nothing mounted on one line; spares; retired (collapsed); add form (mount + copy intervals); rides with no bike (outdoor ones listed, indoor counted)
- [x] Component page: stats, service intervals (edit inline), log (date + notes, delete), bike (mount/move/unmount/retire/back in use), history (edit dates, add a past period), details, delete; link to its rides in Explore
- [x] Ride page: "On the bike" (components on its bike that day). No mirror on the route page (routes have no bike)
- [x] Tests: usage (ride at mounted_at counts, removal exclusive, swap across bikes, reset levels, ride at reset time, km/hours/days, indoor scopes, lifetime), exclusion + date checks, copy schedule, delete guard, cross-component event refused, filter, ride page list, timezone parsing, web flow (133 total)
- [x] Local stack with real data: migration ran at startup; 7 scenario components (chain with wax + outdoor wear check, fork with level 1/2 services, tyre with 90-day sealant, trainer-bike chain + indoor-only trainer cassette, wheelset moved between two bikes, spare chain with copied intervals); /components in ~40 ms; headless check of overview, component pages, ride page, Explore filter (17 rides across both bikes), phone (no horizontal scroll), history save/add with overlap error, quick Log, inline service edit. Scenario components deleted afterwards
- [x] README, .env.example (`TIMEZONE`)
- [ ] Deploy to the Pi (owner): `git pull`, rebuild (migration 0010 runs at startup), then enter the real components
- [ ] Move data to the SSD when it arrives (curated data now exists)

## Who you rode with (2026-10-16, owner request after M6)
VERIFY (2026-10-16): the API gives no list of group members. Activities carry `athlete_count` only (local data: 572 rides at 1, 91 above, max 156); the old "related activities" endpoint isn't in the current API reference; the bulk export's activities.csv has no group column. Mentions `[strava://athletes/<id>/<len>]Name` carry the athlete id; `<len>` is the name's length in characters (checked on real mentions with accented names). They're only in the detail payload and the export description (not the summary).
- [x] Migration 0009: `activity.athlete_count` (normalized, backfilled from the latest raw payload); curated `companion` (name, `strava_athlete_ids[]`) and `activity_companion` (source mention|manual, `hidden` so a removed mention stays removed); backfill of existing mentions (local: 2 people, 6 rides)
- [x] normalize links mentions on every sync (by athlete id, else by a hand-added name, else new)
- [x] Ride page: Riders stat, "Rode with" chips (link to Explore), Add someone (datalist), × remove, "Strava counted a group of N" hint
- [x] Filters `group` (solo/group) and `with` (person), with facet counts; Settings → People (ride counts, rename = merge)
- [x] Tests (120 total), local migration on real data (317 solo, 73 group), headless check of ride page add/remove, Explore, Settings
- [ ] Deploy to the Pi (owner): `git pull`, rebuild (migration 0009 runs at startup)

## M6 — Regions (started 2026-10-16)
VERIFY (2026-10-16): GADM 4.1 is still the current release (no 4.2/5.0 on geodata.ucdavis.edu). One GeoPackage per country with layers `ADM_ADM_0..n`, MULTIPOLYGON/4326, readable with stdlib sqlite3 (GPKG header + WKB) → no GDAL. Sizes vary a lot (CHE 14 MB, ESP 33, FRA 49, USA 113, AUT 171, GBR 431). Levels: FRA 0–5 (5 = 36,611 communes, pre-*communes nouvelles*), ESP 0–4 (8,302 municipios), CHE 0–3 (2,781 communes, pre-merger). `ENGTYPE_n` gives the type label (FRA level 3 says "Districts"); `VARNAME_n` is `|`-separated, sometimes truncated or mis-encoded (e.g. "Andaluc¡a"), "NA" = none; lakes are separate "Water body" regions (skipped). Valencia province has VARNAME València; Valencia municipality comes in 2 pieces (two comarcas).
geoBoundaries considered (owner question): open licences and newer boundaries (CH 2022, 2,286 communes), but no variant names, levels from different sources/years with no parent link, inconsistent type labels → GADM kept (search + hierarchy).
Prototype on local data (FR+ES+CH): 52k regions loaded in 2.5 s, all valid; tagging rides+routes ~1 s; 307/341 rides tagged (the rest: FI, IE, US, AU, DK…). Region data adds ~63 MB to the compressed dump (85 → 148 MB) for three countries → kept out of the nightly dump (re-downloadable; `regions reload` after a restore).
Plan (done 2026-10-16):
- [x] Migration 0008: `region_country` (loaded countries, file name + sha; dumped), `region` (id = GADM GID, stable in URLs/saved views; country, level, name, alt_names, type_label, parent_id, search key, geometry), derived `region_part` (leaf regions subdivided to 255 vertices), `activity_region`, `route_region`
- [x] `velotheque regions load FRA ESP CHE…|file.gpkg`, `regions reload | list | remove`; downloads cached in DATA_DIR/gadm; each country in its own transaction, tags rebuilt once at the end
- [x] Tagging: intersect the deepest level (detail-simplified tracks), add ancestors; on sync, route import, rebuild-derived
- [x] Filter `region` (rides + routes, multi, any level, `none` = untagged, incl. no-GPS rides), facet counts via the registry
- [x] Explore: Region fieldset (countries + picked regions with counts, rides · routes in Both), "Find a place…" (word-prefix on name + variants, unaccented; exact first, then most rides), picked regions outlined on the map (simplified on the fly to 1/2000 of their size)
- [x] Ride and route pages: "Regions" by level, each linking to Explore (shared partial)
- [x] Settings: loaded countries, rides/routes without a region (links to `region=none`)
- [x] Backup: dump-db.sh leaves out region/region_part/activity_region/route_region data (local dump 150 → 85 MB); scratch restore checked (region_country kept, region empty); restore.sh --live prints the reload step; README
- [x] Tests: synthetic GeoPackage (with and without envelope), load/reload/remove, tagging + ancestors, routes, filter + facets + none, search, web, mentions (111 total)
- [x] Real data locally: FRA+ESP+CHE in 20 s (incl. CHE download), reload 15 s; 307/341 tracked rides tagged; Explore with region filters 25–130 ms on the Mac, suggest ~50 ms, France outline 0.26 s; headless check of Explore (filter, search, tick, Both, phone), ride/route pages, Settings
- [ ] Deploy to the Pi (owner): `git pull`, rebuild, `regions load` with the countries ridden; then `sudo ops/install-backup.sh` (dump-db.sh changed)
- Notes: htmx inherits `hx-swap`/`hx-push-url` from the filters form, so the region search input sets its own. Countries in the local data without regions yet: FIN, IRL, USA, AUS, DNK (rides); routes also cross ALB, MNE, GRC, KGZ, IRL, USA/CAN, Eastern Europe.

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
- [x] Top menu back to Explore + Settings (owner): the "Routes" entry was only a shortcut to `/?entity=routes`; routes live in Explore's Rides | Routes | Both toggle
- [x] Clicking a ride/route table row selects it like in Explore (row tint, yellow section kept on the map and as bands on the elevation chart: profile points within 40 m of the section); hover previews others; row again / Esc / click elsewhere (map and chart excepted) deselects. ↺ Reset zoom button under +/− on ride and route maps
- [x] Ride page "On routes" is now the same table as the route page's "Rides on this route" (Name · Route km · On route · Of route), same hover highlight / click zoom; shared `initPairRows` (owner: ride and route pages should feel symmetrical)
- [x] "Rides on this route" collapsible (sections remember open/closed); hovering a row highlights that ride's covered part of the route (fetched once from `/routes/{id}/rides/{ride}.geojson`) and its line, clicking the row zooms to it
- [x] Dropped (owner, not needed): link Strava routes to their Strava page. The export has no route id (`routes.csv` = name + `routes/N.gpx`, empty `<link>` in the GPX) and sync doesn't touch routes, so this needs `GET /athletes/{id}/routes` (needs the `read_all` scope for private routes; check the current token's scope) and a match on name + distance. Owner: not needed (2026-10-16)
- [x] Descriptions show Strava mentions raw (`[strava://athletes/<id>/<name length>]Name`): normalize keeps only the name; migration 0008 cleaned the existing rows (6 locally)
- [!] Photo markers: none of the 517 photos has EXIF GPS or capture time (Strava strips them; media.csv has only filename + caption). Also a spec non-goal. Only lead: undocumented API photo endpoint

## Owner feedback (2026-10-15)
- [x] Clicking a list row (not the name) selects the ride and zooms the map to it; other rides stay drawn; click again to deselect; the name still opens the ride page
- [x] "↺ Reset map" next to the drawing tools: clears the map area + selection, zooms back to all results
- [x] Filter by place name (Aragón, Paris, …) = region filter, M6

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
