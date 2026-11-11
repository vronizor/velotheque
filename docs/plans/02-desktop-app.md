# Plan 2: Vélothèque as a desktop app for friends

Status: proposed (2026-11-11). **Blocked by [Plan 1](01-duckdb-port.md):** the desktop app
needs the database in a single file inside the app process. Start only once the DuckDB port
runs on the Pi and its parity checks have passed.

## Goal

A friend downloads an installer from GitHub Releases, double-clicks it, and has their own
Vélothèque: no terminal, no Docker, no Pi. Their data stays on their computer. When a new
release is tagged, the app offers the update itself.

Out of scope: hosting anyone's data on the Pi, code signing (skipped by choice), mobile,
Proton Drive backups, Tailscale, gpx.studio self-hosting.

## Shape of the app

- **Backend:** the same FastAPI app, frozen with PyInstaller (one folder per OS). It starts on a
  free localhost port, opens the DuckDB file in the OS app-data folder, runs migrations, and
  serves the existing htmx + MapLibre UI unchanged.
- **Shell:** Tauri. A native window points at `http://127.0.0.1:<port>` and runs the backend as
  a sidecar process. Tauri also provides the installers (.dmg, .msi/.exe, AppImage), the
  single-instance lock and the updater. The alternative, pywebview, keeps everything in Python
  but has no updater, so updates would have to be built by hand.
- **Data folder:** Tauri passes its per-OS app-data path to the sidecar as `DATA_DIR`. It holds
  `velotheque.duckdb`, `photos/`, `imports/`, `regions-cache/` and `backups/`.
- **Map tiles:** `map_style_url` (OpenFreeMap) works as is. Maps need internet; everything else
  works offline.
- **Extensions:** ship the spatial (and, if used, fts) binaries for each target platform inside
  the bundle, and install them from the bundled path on first start. Turn autoinstall off.

## Things the Pi does from a terminal that friends need as buttons

| Today (CLI) | Desktop |
|---|---|
| `import-export /data/imports/export_X.zip` | *Import Strava export…*: a native file picker. The sidecar reads the local path directly, so a multi-GB zip is never uploaded |
| `routes import` / `import-library` | Route import already has an upload form; add a *folder…* picker for the library |
| `regions load FRA ESP CHE` / `remove` | Settings → Regions: pick countries, download with progress (GADM, non-commercial licence: fine for personal use) |
| `rebuild-derived`, `normalize` | Settings → Maintenance (added in Plan 1 for the Pi already) |
| Nightly backup | *Export backup…*: the Plan 1 Parquet snapshot written to a folder the user picks, plus *Restore from backup…* |
| Strava connect | Optional (below) |

## Strava

1. **Default: bulk export only.** The friend requests their archive from Strava and imports the
   zip. No credentials; photos included. A *how to get your Strava export* page with
   screenshots ships in the app.
2. **Optional live sync with their own API app.** Settings → Strava has a step-by-step guide to
   strava.com/settings/api, with fields for *their* client ID and secret, stored only in their
   local DB. The authorization callback domain is `localhost` (Strava checks the domain, not the
   port). Limits to state in the guide: one API app per Strava account, a new app is limited to
   its own athlete (which is exactly what they need), and since the 2026 API changes developers
   reportedly need a Strava subscription (VERIFY against Strava's developer docs when writing
   the guide).
3. Never bundle the owner's client secret: a frozen binary can't keep it secret.

## Updates

- Tauri updater plugin, reading `latest.json` from GitHub Releases. On launch it shows *Update
  available → Install & restart*.
- Updates are signed with a Tauri updater key (minisign). That's free and separate from OS code
  signing. Keep the private key in GitHub Actions secrets and a copy offline. Losing it means
  every friend has to reinstall by hand.
- Before migrating on startup, copy `velotheque.duckdb` to `backups/pre-<version>.duckdb`. The
  file isn't open yet at that point, so a plain copy is safe. Keep the last 3.
- Pin the same `duckdb` version on the Pi and in the app, and bump them together. An older
  DuckDB can't read a file written by a newer one, so never let an update downgrade it.

## Build and release (GitHub Actions)

- Trigger: pushing a `v*` tag. Matrix: `macos-14` (Apple Silicon), `windows-latest` (x64),
  `ubuntu-22.04` (x64 AppImage). Add Intel Mac only if a friend needs it.
- Each runner: `uv sync` → PyInstaller onedir → copy the matching DuckDB extension binaries in →
  `tauri-action` builds, signs the update bundle, and attaches everything to a draft release.
- The owner tests the draft on his Mac, then publishes it. Publishing is what makes friends' apps
  see the update.
- Smoke test in CI: launch the frozen sidecar headless, wait for `/` to answer, import
  `scripts/seed_synthetic.py` data, then hit Explore and one area select.

## Unsigned builds (by choice)

- macOS: the first launch is blocked. The friend guide shows *System Settings → Privacy &
  Security → Open Anyway*, once. **VERIFY** whether Tauri updates of an unsigned app trigger the
  prompt again.
- Windows: SmartScreen → *More info → Run anyway*.
- If friends mostly use Macs and this proves annoying, an Apple Developer ID ($99/year) is the
  fix. Revisit after the first round.

## Work order

1. Config: everything Pi-specific (Tailscale base URL, the Proton backup indicator) becomes a
   `deployment = "pi" | "desktop"` setting, so one codebase serves both.
2. The buttons from the table above, built and used on the Pi first (they help there too).
3. PyInstaller spec for the backend; get it running frozen on the Mac.
4. Tauri shell with the sidecar, app-data path, single instance, and a clean shutdown (the
   sidecar must close DuckDB on quit).
5. Onboarding: first-run screen → import export / connect Strava / pick regions.
6. Release workflow, updater key, first draft release, test the update path v0.1.0 → v0.1.1.
7. Friend guide (`docs/install.md`): download, unsigned-app prompts, Strava export, optional API
   setup, where data lives, how to back up.

Rough size: the buttons and config are a weekend. Packaging, CI and the updater are another
weekend or two, most of it fighting per-OS build quirks. After that, each release is just a tag.

## Ongoing cost to accept before starting

Three operating systems that can't be tested locally, bugs reported second-hand, and
migrations running on databases the owner can't see. Pre-migration copies and the Parquet
export are the safety net. Shipping a release becomes a commitment, not just a `git pull`.
