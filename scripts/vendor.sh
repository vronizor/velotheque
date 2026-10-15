#!/usr/bin/env bash
# Fetch pinned frontend libraries into static/vendor (no Node build step).
set -euo pipefail
HTMX=2.0.4
MAPLIBRE=5.6.0
dest="$(dirname "$0")/../src/velotheque/web/static/vendor"
mkdir -p "$dest"
curl -fsSL "https://cdn.jsdelivr.net/npm/htmx.org@${HTMX}/dist/htmx.min.js" -o "$dest/htmx.min.js"
curl -fsSL "https://cdn.jsdelivr.net/npm/maplibre-gl@${MAPLIBRE}/dist/maplibre-gl.js" -o "$dest/maplibre-gl.js"
curl -fsSL "https://cdn.jsdelivr.net/npm/maplibre-gl@${MAPLIBRE}/dist/maplibre-gl.css" -o "$dest/maplibre-gl.css"
printf 'htmx.org %s\nmaplibre-gl %s\n' "$HTMX" "$MAPLIBRE" > "$dest/VERSIONS"
