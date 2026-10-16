# Vélothèque

<img src="docs/logo.png" alt="" width="96" align="right">

A self-hosted explorer for my own Strava cycling history, running on a Raspberry Pi. It's single-user and read-only towards Strava.

Milestones 1–3 and 5 (this version) cover:
- Strava OAuth and a resumable **Sync now** (activities, details, streams)
- the Strava bulk export import, with photos
- normalized rides and tracks
- an **Explore** page: a map, list and totals; filters for date, type, bike, distance, elevation, duration, photos and text search, with live counts next to each option; drawing a rectangle or polygon on the map, or "Filter to this view"; and saved views
- a **Ride** page with a photo gallery
- nightly backups of the database and photos to Proton Drive, a tested restore, and a backup indicator in the header
- a **route library**: GPX import (upload form and CLI, race stages merged into one route), tags, how much of each route you've ridden, and routes on the Explore map

Milestone 4 (components and maintenance) comes later.

The full design is in [`velotheque-spec.md`](velotheque-spec.md), and progress is tracked in [`TODO.md`](TODO.md).

## Stack

| Piece | What |
|---|---|
| `db` | Postgres 18 + PostGIS 3.6, our own image (`docker/db/Dockerfile`) built from the official multi-arch `postgres:18-bookworm` plus PGDG packages |
| `app` | FastAPI + SQLAlchemy Core + Jinja2/htmx, MapLibre GL with OpenFreeMap tiles. Sync runs on a background thread inside the app. |
| Migrations | Alembic, in `src/velotheque/migrations`. Applied automatically when the app container starts. |

### Data layers

| Layer | Tables | Notes |
|---|---|---|
| Raw | `raw_strava_*`, `raw_export_activity`, `raw_gpx_file` | Append-only. A row is only added when the payload changed; GPX files are kept byte for byte, de-duplicated by hash. |
| Normalized | `activity`, `activity_track`, `bike`; `route` geometry, km, m+ | Rebuilt from raw with `velotheque normalize` |
| Derived | `activity_track_simplified`, `route_simplified`, `route_ride_overlap`, `route_coverage` | Rebuilt with `velotheque rebuild-derived` |
| Curated | `saved_view`; `route` name, source, tags, notes and its `route_file` list | Hand-entered, irreplaceable, in the nightly backup |

## Setup on the Pi

```sh
git clone <repo> velotheque && cd velotheque
cp .env.example .env    # then edit it, see below
docker compose up -d --build
```

The app listens on `127.0.0.1:$APP_PORT` (default 8000) and only on the Pi itself. To reach it over HTTPS from your devices, use either option below.

**Option A: Tailscale serve** (no extra software). This gives a valid certificate for the Pi's MagicDNS name, and only your tailnet can reach it:

```sh
sudo tailscale serve --bg 8010        # use the APP_PORT from .env
tailscale serve status                # → https://<pi>.<tailnet>.ts.net proxies to 127.0.0.1:8010
```

The first time, Tailscale may ask you to enable HTTPS certificates for the tailnet; follow its link. To stop serving, run `sudo tailscale serve reset`.

**Option A2: one name per app with [Tailscale Services](https://tailscale.com/docs/features/tailscale-services)**: gives `https://velo.<tailnet>.ts.net`. Tag the Pi (e.g. `tag:server`), define a service `velo` with port `tcp:443` in the admin console, run `sudo tailscale serve --service=svc:velo --https=443 127.0.0.1:8010`, then approve the host on the Services page. Leave Funnel off. Set `BASE_URL` and the Strava callback domain to that name.

**Option B: an existing Caddy on the host**:

```caddy
mypi.my-tailnet.ts.net {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
```

### Environment variables (`.env`)

| Variable | Purpose |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Database owner and name (used by the `db` container and by compose to build the app's URL) |
| `VELOTHEQUE_RO_PASSWORD` | Password for the read-only role `velotheque_ro`, created on first DB init |
| `DATABASE_URL` | Only for running the CLI or tests on the host (`localhost:5432`). The app container gets its own. |
| `VELOTHEQUE_DATA_DIR` | Host directory on the USB SSD. Postgres data goes in `$VELOTHEQUE_DATA_DIR/postgres`. |
| `TAILSCALE_IP` | Interface the Postgres port is published on, so you can reach it from your SQL client over Tailscale. Defaults to `127.0.0.1`. |
| `APP_PORT` | Host port the app listens on, on 127.0.0.1 only. Defaults to 8000; change it if something else uses 8000. |
| `BASE_URL` | Public URL of the app, e.g. `https://mypi.my-tailnet.ts.net` |
| `OAUTH_BASE_URL` | Optional override for the OAuth redirect only (see the fallback below) |
| `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` | From <https://www.strava.com/settings/api> |
| `STRAVA_API_BASE` | Defaults to `https://www.strava.com/api/v3`. Strava announced a move to `api-v3.strava.com` in 2027. |
| `MAP_STYLE_URL` | Defaults to OpenFreeMap Liberty |

## Connecting Strava

1. On <https://www.strava.com/settings/api>, set **Authorization Callback Domain** to the bare host of `BASE_URL`, with no scheme or port (e.g. `mypi.my-tailnet.ts.net`).
2. Open **Settings → Connect to Strava** and leave every permission ticked. The app needs `activity:read_all` (private rides) and `profile:read_all` (bikes).
3. The token is stored in `strava_auth` and refreshed automatically. The read-only role can't see it.

The access token shown on Strava's API page only has `read` scope, so it isn't used.

**Fallback if Strava rejects the Tailscale hostname:**
- `localhost` is always accepted as a redirect host.
- Set `OAUTH_BASE_URL=http://localhost:8000`, restart the app, then run `ssh -L 8000:127.0.0.1:8000 user@<pi>`.
- Open <http://localhost:8000/settings> (it must be localhost, so the state cookie matches) and click Connect.
- This is needed once; token refreshes don't use the redirect.

## Syncing

- **Sync now** (in the header) or `docker compose exec app velotheque sync` runs these steps:
  1. Fetch the athlete and bikes.
  2. List activities from the newest one minus 14 days. The first run lists everything.
  3. Normalize them right away. Each ride gets a provisional track from Strava's polyline, so the map works immediately.
  4. Fetch details and full GPS streams, newest first, for new or changed rides.
- **Rate limits** are read from Strava's response headers. When fewer than 3 calls remain in the 15-minute or daily window, the job stops with status `rate_limited` and shows when to try again. The next click resumes where it stopped.
- **Change detection** ignores kudos and other counters. A rename, a different bike or a cropped distance triggers a re-fetch.
- **Fixing a wrong title or bike:** edit it on Strava, then click **Refresh from Strava** on the ride page.
- **Rides that are left out:** only cycling sport types are normalized. Other activities are kept in the raw tables.

## Importing the Strava bulk export

The export fills in your history without spending Strava API calls. Request it on strava.com under Settings → My Account → Download or Delete Your Account. Then:

```sh
cp export_XXXX.zip "$VELOTHEQUE_DATA_DIR/imports/"
docker compose exec app velotheque import-export /data/imports/export_XXXX.zip
```

What the import does:
- Stores each `activities.csv` row raw (append-only, keyed by activity ID) and copies the track files to `$VELOTHEQUE_DATA_DIR/strava-export/<export>/`.
- Normalizes the cycling rows and builds their tracks from the `.fit` / `.gpx` / `.tcx` files, gzipped or not.
- Maps bikes exactly, through the CSV's `Bike` column (the Strava gear ID). Cycling rides whose gear can't be mapped are listed at the end of the run.
- Can be re-run safely: unchanged rows are skipped.

How it combines with the API:
- **Metadata:** when both sources exist, the API wins (name, type, time zone, bike). Export-only rides show UTC times until a sync supplies their real time zone.
- **Tracks:** the priority is API streams, then the export's track file, then Strava's coarse polyline.
- **API budget:** a sync skips the detail and streams calls for rides the export already covers, so after an import a sync only costs a handful of API calls.

Photos and videos are copied to `$VELOTHEQUE_DATA_DIR/photos/<strava activity id>/`, and about 400 px thumbnails are made under `thumbs/`.

Strava's API has no documented endpoint that lists every photo of an activity. So for rides newer than your export, a sync stores only the **primary** photo, taken from the activity details at no extra API cost. Importing a newer export later fills in the rest. Photos are de-duplicated by Strava's photo UUID, which is also the export's media file name.

## Routes

A route is a GPX file (or several merged ones) that you keep for inspiration. Each one has a name, a source (free text, e.g. `Strava`, `gpx.studio`, `file`, `Komoot`), free-form tags and notes. There's no to-do status: how much of a route you've ridden is computed from your rides.

**Importing.** Use **Routes → + Import GPX** in the app: choose several files at once; tick “Merge” to turn the stages of a race into one route (they're ordered by file name, `s2` before `s10`). Or use the CLI:

```sh
docker compose exec app velotheque routes import-library /data/imports/gpx-library --separate personal   # one route per file, one merged route per subfolder (except --separate ones)
docker compose exec app velotheque routes import /data/imports/gpx/*.gpx --tag bikepacking
docker compose exec app velotheque routes import --merge --tag race /data/imports/gpx/some-race/*.gpx
docker compose exec app velotheque routes import-export /data/imports/export_XXXX.zip   # routes drawn on Strava
```

- GPX tracks (`<trk>`) and routes (`<rte>`) are read; `.tcx` and `.fit` also work, gzipped or not. Each track in a file stays its own piece, so a file with one track per stage doesn't count the jumps between stages as distance. A file with several tracks is named after its metadata or its file name.
- The files are stored in the database exactly as uploaded (so the nightly backup has them) and can be downloaded from the route page. A file that is already part of a route is skipped, so imports can be re-run safely; deleting a route keeps its files, and re-importing them brings it back.
- Strava export routes come in with `source = Strava`, so one tick hides them; running routes are skipped and mountain-biking routes get the `mtb` tag.
- Elevation gain counts a climb once it rises 10 m, which matched Strava's totals on 330 real rides (median ratio 0.997). Files without elevations show “–”.

**Ridden.** A stretch of route counts as ridden when it lies within 30 m of a ride's track; stretches under 100 m (road crossings) are ignored.
- *Overlap* (per ride): the share of the route that ride covers. *Ridden* (per route): the share covered by all your rides together, counted once.
- Both are measured on the route's distinct roads: an out-and-back stretch is covered by riding it once.
- They update when a route is imported and when a sync brings new rides; `rebuild-derived` recomputes everything (about 20 s for 88 routes × 341 rides on the Mac).

**In Explore**, the Rides | Routes | Both toggle switches lists; routes are dashed blue lines on the map. Tick routes in the list to set their source or add/remove tags in one go. Route filters: tags, source, and ridden % (e.g. max 10% for routes you've barely touched); search, distance, elevation and map areas apply to routes too. The route page shows the ridden parts in dark blue, has the same elevation chart as a ride (with section measuring), and lists the rides on it (hover a row to see the part that ride covered on the map and the chart; click it to select it and zoom there, click again, press Esc or click elsewhere to deselect; ↺ under the zoom buttons resets the view), with a link that opens them in Explore (`overlaps_route=<id>:<min %>`, 30% when no minimum is given). A ride's page has the same table of the routes it rides (collapsed under "On routes") and can draw them on its map ("show the routes it rides"); the route page can hide its rides the same way.

**Ride page:** the map can be resized by dragging the bar under it. The elevation chart is built from the altitude and distance streams, with about 1,000 samples. Hovering it shows km, altitude and grade, and a dot on the map. The grade is averaged over ±100 m so GPS noise doesn't show as 30% ramps. Hovering the track on the map moves the chart cursor. Drag across the chart to measure a section: distance, ↗ gain and ↘ loss (same 10 m rule as the totals), average grade, highlighted in yellow on the map, with "Zoom map"; a click or Esc clears it. Videos open in the same lightbox as photos (‹ › or the arrow keys).

## Search and filters

- **One state everywhere:** all filters live in the URL, so every view can be bookmarked, the back button works, and a saved view is just a named query string.
- **How filters combine:** values within one field are ORed, and different fields are ANDed.
- **Counts:** the number next to each type, bike or photos option counts every other active filter except that field's own. It shows what ticking that option would give.
- **Selecting a ride:** click a row in the list (anywhere but its name) to highlight that ride and zoom the map to it, with all other rides still drawn. Click the row again to deselect. Clicking the name opens the ride page.
- **Search** (`q`) matches the start of every word in the name and description, ignoring accents (`valencia` finds “València”). Names also get a typo-tolerant match (`galibeir` finds “Galibier”). There's no language stemming, because titles mix French, Spanish, Catalan and English.
- **Reset filters** clears every filter but keeps the Rides / Routes / Both choice.
- **Map / list split:** drag the bar above the list (or focus it and use ↑/↓) to give the map more or less room; the browser remembers it, and a double-click resets it. Phones keep the stacked layout.
- **Map area:**
  - **Rectangle**: click two opposite corners.
  - **Polygon**: click the corners, then click the first point again to close it.
  - **Filter to this view**: uses the visible map.
  - **Reset map**: clears the map area and the selected ride, and zooms back to all results.
  - A ride matches if its track crosses the area. Panning on its own never filters.

## Backup to Proton Drive

> **A backup isn't trusted until a restore has been tested.** Run the restore test below after setting this up, and again from time to time.

**Schedule:** a systemd timer runs every night at about 03:30.

**Each run:**
1. **Dump** (as root, only to reach Docker): `pg_dump -Fc` from the `db` container into `/var/lib/velotheque-backup/dumps/`. The archive is checked with `pg_restore --list` before anything is uploaded.
2. **Upload** (as the unprivileged `velobackup` user, the only account holding the Proton session):
   - the dump to `Velotheque/db/velotheque-<UTC date>T<time>Z.dump`
   - photos and videos not yet in the local manifest (`photos-manifest.tsv`) to `Velotheque/photos/<activity>/`, in batches of 200. Thumbnails aren't uploaded; they're rebuilt after a restore.
3. **Prune:** keep the newest dump of each of the last 14 days and of each of the last 8 ISO weeks. The rest is moved to the Proton Drive trash.
4. **Record** the result (as root): a `backup_status` row, which drives the header dot.
   - green: the last success is under 48 h old
   - amber: the last run failed, but a recent success exists
   - red: no successful backup in 48 h

The Proton-specific code is all in `ops/proton_backup.py` (Python standard library only), so swapping the tool later means changing one small file.

### What I verified about the `proton-drive` CLI (v0.9.0)

- **Builds:** there's an official Linux arm64 (glibc) build, with SHA-512 checksums on Proton's download page.
- **Deleting:** it can delete remote files (`filesystem delete` and `filesystem trash`). Old dumps are pruned by name, not by overwriting fixed file names.
- **Headless login:** `auth login` prints a URL you can open on another device. Where the session is stored is set by `PROTON_DRIVE_CREDENTIALS_STORE`:
  - `keychain` (default): needs a desktop keyring
  - `pass`: GPG-encrypted, the documented headless option
  - `unsafe_file`: plain JSON, which Proton calls testing only
- **Version pin:** the CLI is pre-1.0, so the backup refuses to run unless `proton-drive --version` matches `PROTON_DRIVE_VERSION`.
- **Checked live against a throwaway Drive folder** (all undocumented):
  - `filesystem list --json` returns an array of nodes whose `name` is an object, `{"ok": true, "value": "…"}`.
  - Errors exit with code 1 and a message on stderr, such as `You need to login first`.
  - Uploading a folder with `--folder-conflict-strategy merge` creates any missing remote folders in one call.
  - Re-uploading unchanged files is skipped, so the second run took 4.5 s against 22 s for the first.
  - `filesystem delete` only works on items already in the trash. Pruning therefore only trashes, and Proton (or you) empties the trash.
  - Per-command `--help` doesn't work in 0.9.0.

### Setup on the Pi (once)

```sh
# 1. The CLI: download the linux/arm64 build from https://proton.me/drive/download,
#    check its SHA-512 against the published one, then:
sudo install -m 0755 proton-drive /usr/local/bin/proton-drive
proton-drive --version            # must match PROTON_DRIVE_VERSION in backup.env

# 2. User, state dir, systemd units and /etc/velotheque/backup.env
sudo apt install pass             # for PROTON_DRIVE_CREDENTIALS_STORE=pass
sudo ops/install-backup.sh        # creates backup.env on the first run; edit it, run again

# 3. A GPG key and password store for the backup user (no passphrase: the job runs
#    unattended, so file permissions are what protect it; the home dir is 700)
sudo -u velobackup -H gpg --batch --pinentry-mode loopback --passphrase '' \
  --quick-gen-key velotheque-backup default default never
sudo -u velobackup -H pass init velotheque-backup

# 4. Log the backup user in. Open the printed URL on your laptop and finish there.
sudo -u velobackup -H env PROTON_DRIVE_CREDENTIALS_STORE=pass proton-drive auth login

# 5. First run (also uploads every photo once), then check
sudo systemctl start velotheque-backup
journalctl -u velotheque-backup -n 50 --no-pager
```

Re-run `sudo ops/install-backup.sh` after every `git pull` that changes `ops/`.

**When the Proton session expires**, the next run fails: the header dot turns amber, then red after 48 h, and Settings shows the error. Log in again with step 4, then `sudo systemctl start velotheque-backup`.

**If `pass` is too fiddly**, set `PROTON_DRIVE_CREDENTIALS_STORE=unsafe_file` in `backup.env` and redo step 4. The session is then a plain file under `/var/lib/velotheque-backup/.local/share/proton-drive-cli/`. The script keeps it `chmod 600` in a `700` directory owned by `velobackup`.

### Restore

```sh
# Restore test: newest dump from Proton Drive into a scratch database, compared with live
sudo ops/restore.sh --latest-from-drive
#   → "RESTORE TEST OK: row counts match the live database"
sudo ops/restore.sh --drop-scratch

# Or from a local file (e.g. /var/lib/velotheque-backup/dumps/…)
sudo ops/restore.sh /var/lib/velotheque-backup/dumps/velotheque-2026-10-15T0330Z.dump

# Real disaster recovery: replace the live database (stops the app; asks you to type its name)
sudo ops/restore.sh --live /path/to/velotheque-….dump
```

To restore photos, download the folder back, then rebuild the thumbnails and photo rows:

```sh
sudo -u velobackup -H env PROTON_DRIVE_CREDENTIALS_STORE=pass proton-drive filesystem download \
  --file-conflict-strategy skip --folder-conflict-strategy merge \
  /my-files/Velotheque/photos /tmp/photos-restore
sudo rsync -a /tmp/photos-restore/photos/ "$VELOTHEQUE_DATA_DIR/photos/"
docker compose exec app velotheque normalize
```

Tested on the Mac with the real CLI and a throwaway Drive folder:
- the full nightly run took 22 s (a 51 MB dump and 2 photos)
- pruning with tight retention trashed exactly the expected dumps
- `restore.sh --latest-from-drive` downloaded and restored the dump in 13 s, with row counts matching the live database

## CLI

```sh
docker compose exec app velotheque migrate           # apply migrations (also done at startup)
docker compose exec app velotheque sync              # same as the Sync now button
docker compose exec app velotheque sync --activity 1234567890
docker compose exec app velotheque import-export /data/imports/export_XXXX.zip
docker compose exec app velotheque routes import FILE… [--merge] [--name N] [--source S] [--tag T]
docker compose exec app velotheque routes import-library DIR [--separate SUBFOLDER] [--source S] [--tag T]
docker compose exec app velotheque routes import-export /data/imports/export_XXXX.zip
docker compose exec app velotheque routes normalize  # recompute routes from their files (e.g. after an upgrade adds a field)
docker compose exec app velotheque normalize         # rebuild normalized tables (rides and routes) from raw (--force-tracks re-parses export files)
docker compose exec app velotheque rebuild-derived   # drop and rebuild derived tables
docker compose exec app velotheque backup-status --ok|--error MSG   # used by the backup service
```

## Read-only SQL access

- The role `velotheque_ro` is created on first DB init.
- It gets `SELECT` on every table except `strava_auth`, including tables added by later migrations.
- Connect from your client to `TAILSCALE_IP:5432` as `velotheque_ro`.

## Development (Mac)

```sh
uv sync
docker compose up -d db
uv run velotheque migrate
uv run uvicorn velotheque.web.app:app --reload        # http://localhost:8000
uv run pytest                                        # uses a velotheque_test database it creates itself
uv run ruff check && uv run ruff format --check
```

For performance checks, load 5,000 synthetic rides into a scratch database:

```sh
DATABASE_URL=postgresql+psycopg://velotheque:<pw>@localhost:5432/velotheque_seed \
  uv run python scripts/seed_synthetic.py 5000
```

Measured on an M-series Mac with 5,000 rides (282,000 km): the unfiltered map is 3.7 MB gzipped and takes 0.58 s; filtered views take under 0.15 s. Pi numbers are still to come.

Frontend libraries are vendored. `scripts/vendor.sh` fetches the pinned htmx and MapLibre versions into `src/velotheque/web/static/vendor/`.
