#!/usr/bin/env bash
# Fetch pinned frontend libraries into static/vendor (no Node build step).
set -euo pipefail
HTMX=2.0.4
MAPLIBRE=5.6.0
TERRADRAW=1.35.0
TERRADRAW_MAPLIBRE=1.4.1
dest="$(dirname "$0")/../src/velotheque/web/static/vendor"
mkdir -p "$dest"
curl -fsSL "https://cdn.jsdelivr.net/npm/htmx.org@${HTMX}/dist/htmx.min.js" -o "$dest/htmx.min.js"
curl -fsSL "https://cdn.jsdelivr.net/npm/maplibre-gl@${MAPLIBRE}/dist/maplibre-gl.js" -o "$dest/maplibre-gl.js"
curl -fsSL "https://cdn.jsdelivr.net/npm/maplibre-gl@${MAPLIBRE}/dist/maplibre-gl.css" -o "$dest/maplibre-gl.css"
curl -fsSL "https://cdn.jsdelivr.net/npm/terra-draw@${TERRADRAW}/dist/terra-draw.umd.js" -o "$dest/terra-draw.umd.js"
curl -fsSL "https://cdn.jsdelivr.net/npm/terra-draw-maplibre-gl-adapter@${TERRADRAW_MAPLIBRE}/dist/terra-draw-maplibre-gl-adapter.umd.js" \
  -o "$dest/terra-draw-maplibre-gl-adapter.umd.js"
printf 'htmx.org %s\nmaplibre-gl %s\nterra-draw %s\nterra-draw-maplibre-gl-adapter %s\n' \
  "$HTMX" "$MAPLIBRE" "$TERRADRAW" "$TERRADRAW_MAPLIBRE" > "$dest/VERSIONS"
