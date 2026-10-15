# Vélothèque

<img src="docs/logo.png" alt="" width="96" align="right">

A self-hosted explorer for my own Strava cycling history, running on a Raspberry Pi. It's single-user and read-only towards Strava.

Milestone 1 (this version) covers:
- Strava OAuth and a resumable **Sync now** (activities, details, streams)
- normalized rides and tracks
- an **Explore** map and list with date, type, bike, distance, elevation and duration filters
- a **Ride** page

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
| Raw | `raw_strava_*` | Append-only. A row is only added when the payload changed. |
| Normalized | `activity`, `activity_track`, `bike` | Rebuilt from raw with `velotheque normalize` |
| Derived | `activity_track_simplified` | Rebuilt with `velotheque rebuild-derived` |
| Curated | — | Arrives in later milestones and is the only irreplaceable data |

## Setup on the Pi

```sh
git clone <repo> velotheque && cd velotheque
cp .env.example .env    # then edit it, see below
docker compose up -d --build
```

The app listens on `127.0.0.1:8000`. Put it behind the existing Caddy:

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

Photos from the export (`media/`) aren't imported yet. That's planned for Milestone 2.

## CLI

```sh
docker compose exec app velotheque migrate           # apply migrations (also done at startup)
docker compose exec app velotheque sync              # same as the Sync now button
docker compose exec app velotheque sync --activity 1234567890
docker compose exec app velotheque import-export /data/imports/export_XXXX.zip
docker compose exec app velotheque normalize         # rebuild normalized tables from raw (--force-tracks re-parses export files)
docker compose exec app velotheque rebuild-derived   # drop and rebuild derived tables
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
