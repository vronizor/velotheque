# Vélothèque — build spec (v2, October 2026)

Self-hosted web app on a Raspberry Pi for exploring my own Strava cycling history: a filterable map of all rides, a GPX route library compared against what I've ridden, and component/maintenance tracking.

Single user. Laptop-first. Read-only with respect to Strava.

**How to work on this:** start in plan mode. Propose the schema and the Milestone 1 plan before writing code. Build milestone by milestone; each one must leave the app usable. Where this spec says **VERIFY**, check against current docs or real data before relying on it, and tell me what you found.

---

## Prerequisites (mine, before starting)

1. Register a Strava API application at https://www.strava.com/settings/api (client ID + secret). I have an active Strava subscription, which Standard-tier API access requires.
2. Postgres data directory and photo storage live on the Pi's USB SSD, not the SD card.
3. Request a Strava bulk export (Settings → My Account → Download or Delete Your Account → Request archive) for the historical backfill.
4. Install and log in to Proton's official `proton-drive` CLI on the Pi (needed from Milestone 3).

---

## Architecture

| Concern | Decision |
|---|---|
| Database | Postgres + PostGIS, dedicated container for this project |
| PostGIS image | The official `postgis/postgis` images are still amd64-only. Build our own image `FROM postgres:<major>-bookworm` (official, multi-arch) and install `postgresql-<major>-postgis-3` from the Debian/PGDG repos. Don't depend on third-party arm64 images. |
| Migrations | Alembic from the first table |
| Backend | FastAPI, Python 3.12+, SQLAlchemy 2 (Core for the filter/query layer; ORM optional for simple CRUD) |
| Frontend | Server-rendered Jinja2 templates + HTMX. MapLibre GL JS for maps, Terra Draw (or equivalent) for drawing rectangles/polygons. No Node build step; vendor the JS/CSS into `static/`. |
| Basemap | Vector tiles from OpenFreeMap by default (no API key). Style URL configurable via env. |
| Containers | Docker Compose with two services: `db` and `app`. Sync runs as a background job inside `app`. Backup runs on the host (see Backup). |
| Access | Existing Caddy reverse proxy + Tailscale. No public exposure, no auth layer. |
| Ingestion | Manual "Sync now" button calling the Strava API. No webhook, no scheduled polling. One-time backfill from the bulk export. |

### Data layering (non-negotiable)

- **Raw**: source payloads stored untouched and never mutated. Tables: `raw_strava_activity` (API JSON), `raw_strava_streams`, `raw_export_activity` (bulk export row + original file reference), `raw_gpx_file`. Keyed by source ID, with `fetched_at`.
- **Normalized**: our own schema, populated only from raw by a normalize step. Field names are source-agnostic.
- **Derived**: computed, never hand-edited, safe to drop and rebuild with one command (`velotheque rebuild-derived`). Includes simplified geometries, region tags, and route/ride overlaps.
- **Curated**: things I enter by hand: bikes metadata beyond Strava, components, assignments, service types/events, routes' status and notes, saved views. **This is the only irreplaceable data**, and backup priorities follow from that.

Ingestion and normalization are idempotent. Re-running anything must never duplicate or corrupt data.

---

## Ingestion

### Strava OAuth

- Scopes: `read,activity:read_all,profile:read_all` (private rides included; gear list via the athlete endpoint).
- Store the refresh token in the DB. Client ID/secret in `.env`, not committed.
- The OAuth redirect happens in my browser, so the callback only needs to be reachable from my laptop, not from the internet. **VERIFY** that Strava's "Authorization Callback Domain" accepts the Pi's Tailscale MagicDNS hostname. If not, document a one-time `localhost` + SSH tunnel flow.
- A settings page shows connection status and a "Connect to Strava" button.

### Sync now

- A button in the header starts a background job. A `sync_job` table records start, end, status, counts, and errors. The UI polls job status with HTMX.
- Steps:
  1. List activities since the most recent synced start date minus a 14-day overlap window, so recent edits are picked up.
  2. For new activities, or ones whose summary changed (name, distance, gear), fetch the detailed activity (needed for `description`), streams (`latlng,altitude,time,distance,moving`), and photos.
  3. Normalize, then compute derived data for the affected rides only.
- Rate limits: don't hardcode numbers. Read the rate-limit headers on every response, stop cleanly before hitting the limit, and record progress. The next click resumes.
- The ride page has a per-ride "Refresh from Strava" button. To fix a wrong bike or title, I fix it on Strava and refresh. No local overrides.
- Indoor/virtual rides without GPS are stored and filterable, but have no track.

### Bulk export backfill

- CLI: `velotheque import-export <path-to-zip>`.
- Parse `activities.csv` with **polars**. **VERIFY** the column names against the real export (expected: activity ID, name, description, type, gear, filename, media).
- Parse track files (`.fit.gz` / `.gpx.gz` / `.tcx.gz`) into tracks.
- Import photos from the `media/` folder, linked via the CSV's media column.
- Deduplicate on Strava activity ID. When both export and API data exist for a ride, the API record wins for metadata.
- Map export gear names to Strava gear IDs via the athlete's gear list. Rides whose gear can't be matched are listed for manual review.

### Photos

- Store under `/data/photos/{activity_id}/` on the SSD and generate ~400px thumbnails on import.
- **VERIFY** which API endpoint gives full-resolution URLs for all photos of an activity. The detailed activity payload only includes the primary photo. If no stable endpoint exists, new rides get their primary photo via the API and a periodic re-import of the bulk export fills in the rest. Say which approach you took.

---

## Schema (starting point; evolve via migrations)

**Rides**
- `activity`: id, source, source_id, name, description, type, start_at (timestamptz), start_at_local, timezone, distance_m, moving_time_s, elapsed_time_s, elevation_gain_m, bike_id, has_photos, synced_at.
- `activity_track`: activity_id, geometry LineString/MultiLineString in SRID 4326, plus raw point arrays if needed.

**Bikes and components**
- `bike`: id, strava_gear_id (nullable), name, retired.
- `component`: id, name, kind, brand, model, notes, retired_at.
- `component_assignment`: component_id, bike_id, mounted_at (timestamptz), removed_at (nullable = current). Use an exclusion constraint (`btree_gist`, `tstzrange`) so a component can't be on two bikes at once.
- `service_type`: id, component_id, name, level (int), interval_value, interval_unit (`hours` | `km`).
- `service_event`: id, component_id, service_type_id, performed_at, notes.

**Routes**
- `route`: id, name, status (`wishlist` | `planned` | `favorite` | `done`), source (`gpx_studio` | `strava` | `manual`), notes, geometry (same type and SRID as `activity_track`), distance_m, elevation_gain_m (from GPX elevations if present), created_at.

**Regions**
- `region`: id, country, gadm_level, name, alt_names[], type_label (e.g. "Commune", "Municipio", "Province"), parent_id, geometry.

**Other**
- `saved_view`: id, name, entity, query_string.
- `sync_job`, `backup_status`.

**Derived tables**
- `activity_track_simplified` and `route_simplified`: two simplification levels each (overview, detail).
- `activity_region`, `route_region`: (entity_id, region_id).
- `route_ride_overlap`: (route_id, activity_id, overlap_fraction) = length of the route within 30 m of the ride ÷ route length. Use geography or a metric projection, not degrees. Compute only for rides whose bbox intersects the route's.
- `route_coverage`: route_id, fraction of the route within 30 m of *any* ride.
- Full-text search vectors.

---

## The filter model (core of the app)

One filter state drives everything. It is serialized in the URL query string, so every view is bookmarkable, the back button works, and saved views are just named query strings.

**Semantics:** OR within a field, AND across fields. Nested boolean logic is deliberately not supported.

**Field registry.** Each filter field is registered once with:
- its URL param and type
- which entities it applies to (rides, routes, or both)
- a function returning a SQLAlchemy where-clause
- a facet-count query, if it is facetable

Adding a filter later means registering one field, nothing else.

| Field | Applies to | Notes |
|---|---|---|
| `q` | rides, routes | full-text over name/description/notes |
| `after`, `before` | rides | on start_at_local |
| `type` | rides | multi |
| `bike` | rides | multi |
| `component` | rides | rides done while that component was mounted |
| `dist_min`, `dist_max` | rides, routes | km |
| `elev_min`, `elev_max` | rides, routes | m |
| `dur_min`, `dur_max` | rides | moving time |
| `region` | rides, routes | multi, any GADM level |
| `bbox` | rides, routes | rectangle drawn on the map |
| `area` | rides, routes | polygon drawn on the map, encoded polyline to keep URLs short |
| `photos` | rides | has photos |
| `status` | routes | multi |
| `overlaps_route` | rides | rides overlapping route X by ≥ N% (default 30%) |
| `coverage_min`, `coverage_max` | routes | e.g. wishlist routes I've barely ridden |

**Facet counts.** Each facetable field shows counts per value, computed with all active filters except that field's own. This is standard faceted-search behavior: counts show what selecting a value would give.

**Text search.** Titles mix French, Spanish, Catalan and English, so don't use language-specific stemming.
- Use the `simple` text-search config plus `unaccent`, and `pg_trgm` for fuzzy/typo-tolerant matching on names.
- `unaccent()` isn't IMMUTABLE, so wrap it in an immutable function to use it in generated columns or indexes.
- Region search matches `name` and `alt_names`, accent-insensitively, so "Valence", "València" and "Valencia" all match.

**Performance target.** The Explore page responds in under 1 s with ~5,000 rides on the Pi.
- Serve simplified geometries as GeoJSON for the filtered set.
- Only move to PostGIS vector tiles (`ST_AsMVT`) if payloads become a real problem. Measure first.

---

## Pages

**Explore (home)**
- Filter sidebar with facet counts.
- Entity toggle: Rides | Routes | Both.
- Map of results: rides as solid lines with low opacity, so overlaps build up into a heatmap effect; routes as dashed lines in another color.
- Sortable result list.
- Totals bar: count, km, elevation, hours.
- Hovering or clicking a list row highlights it on the map, and the reverse.
- Draw-rectangle and draw-polygon tools set `bbox`/`area`.
- Panning the map does **not** filter. An explicit "Filter to this view" button does.
- "Save view" stores the current query string under a name.

**Ride**: map of the track, stats, description, photo gallery, bike and the components mounted at the time, regions crossed, overlapping routes, and the "Refresh from Strava" button.

**Route**
- Map of the route with overlapping rides, coverage %, and the list of overlapping rides.
- Editable status and notes.
- GPX upload form, used for import together with a CLI equivalent (`velotheque routes import file.gpx --status wishlist`).
- This reverses the July decision ("CLI only, no upload UI"). With a web app already in place, the upload form is trivial and beats scp-ing files from my laptop.

**Components**
- Per bike: components currently mounted, each service type as a progress bar against its interval, turning **red** when exceeded. No notifications.
- Forms to log a service event, mount/unmount a component, and add components and service types.
- Per component: assignment history.
- A list of rides with no bike assigned.

**Settings**: Strava connection, sync history, backup status.

The header shows the "Sync now" button and a backup indicator that turns red if the last successful backup is older than 48 h.

### Component usage rules

- Usage for a service type = sum of `moving_time_s` (hours) or `distance_m` (km) over rides where:
  - the ride's bike = the assignment's bike,
  - `start_at` falls within `[mounted_at, removed_at)`,
  - and `start_at` is after the most recent reset for that service type.
- **Reset rule:** a service event of level L resets every service type on that component with level ≤ L. For example, big service = level 2 and small service = level 1, so a big service resets both.
- Compute usage on demand (a SQL function or view). The data is small; don't store it.

---

## Regions (geotagging)

- Source: **GADM** boundaries, chosen because municipality-level coverage is consistent across FR/ES/CH. GADM's license is non-commercial, which fits personal use.
- CLI: `velotheque regions load FR ES CH ...` loads all levels for the given countries.
  - Store `type_label` from GADM's English type field, so the UI can show "València — Municipality" vs "Valencia — Province".
  - Keep `alt_names` from GADM's variant-name field.
  - Use full-resolution geometry for tagging and simplified geometry for display.
- The Settings page shows how many rides have no region tag, as a hint that a country is missing.

---

## Backup → Proton Drive (Milestone 3, before any curated data exists)

The backup runs on the host, not in a container, because Proton's official `proton-drive` CLI keeps a login session. It is triggered by a systemd timer. Scripts and unit files live in the repo under `ops/`.

- **Nightly:** `pg_dump -Fc` from the `db` container, uploaded to a `Velotheque/db/` folder on Proton Drive with a dated filename.
  - Retention: 14 daily + 8 weekly.
  - **VERIFY** that the CLI supports deleting remote files. If not, rotate by overwriting a fixed set of filenames.
- **Photos:** incremental. Upload only files not already recorded in a local manifest.
- **Headless session:** **VERIFY** the CLI's documented mode for headless systems without a desktop keyring.
  - Run the backup as a dedicated user.
  - Session file `chmod 600`.
  - Document how to re-authenticate when the session expires.
- On success, write a timestamp to `backup_status` for the header indicator. On failure, write the error.
- **Restore:** provide `ops/restore.sh` and document a restore test into a scratch database. The README says a backup isn't trusted until a restore has been tested.
- The CLI is pre-1.0. Pin the version and keep the upload logic in one small script, so it can be swapped for another tool if needed.

---

## Milestones

1. **Skeleton + sync:**
   - compose and the PostGIS image, migrations, and Strava OAuth
   - Sync now (activities, details, streams)
   - normalized activities and tracks with simplified geometries
   - Explore with map, list, totals, and filters for date/type/bike/distance/elevation/duration
   - Ride page
2. **History + search:**
   - bulk export import, including photos
   - photo gallery, full-text search, facet counts
   - bbox/area drawing, saved views
3. **Backup to Proton Drive**, with the header indicator and a tested restore.
4. **Components and maintenance.**
5. **Routes:** GPX import (form + CLI), route pages, overlap/coverage derived tables, routes in Explore.
6. **Regions:** GADM load, tagging, region filter and search.

## Testing priorities

Bugs in these places would silently produce wrong numbers, so test them first:
- **Filter builder:** each field; OR-within/AND-across semantics; facet counts excluding their own field.
- **Component usage:** swaps across bikes, a ride starting exactly at `mounted_at`, reset levels, km vs hours.
- **Route overlap fraction** on synthetic geometries.
- **Export parser** against a small fixture export.

## Conventions

- `uv` for dependencies, `ruff` for lint and format, `pytest`.
- All tabular data transformation in **polars**, never pandas. Pandas only where a library strictly requires it as input.
- Keep code comments to a minimum.
- Keep dependencies few. Prefer a SQL query over a new library.
- The README covers setup, env vars, the Strava connection, export import, region loading, and backup/restore.

## Explicit non-goals

- Fitness, power, or training-load analytics
- Multi-user support or an auth layer
- Notifications of any kind
- Strava webhooks or scheduled polling
- Writing anything back to Strava
- "Who I rode with"
- An in-app SQL console. I'll query Postgres from my own client over Tailscale using a read-only role, so create that role.
- Nested boolean filters
- Route scoring beyond overlap/coverage %
- Mobile-optimized UI (it shouldn't break on a phone, but it's designed for a laptop)
- Photo markers on the map
