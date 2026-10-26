"use strict";

const COLORS = { ride: "#D63A2A", foot: "#2F8F4E", route: "#1F5FAD", covered: "#0B3C7A", highlight: "#161412", selection: "#F2B705", region: "#6B4FA0" };
const SOURCES = ["routes", "rides"];
const ACTIVE = [
  "any",
  ["boolean", ["feature-state", "hover"], false],
  ["boolean", ["feature-state", "selected"], false],
];

function boundsOf(bbox) {
  return [[bbox[0], bbox[1]], [bbox[2], bbox[3]]];
}

function encodePolyline(points) {
  let out = "";
  let prevLat = 0;
  let prevLng = 0;
  for (const [lat, lng] of points) {
    const iLat = Math.round(lat * 1e5);
    const iLng = Math.round(lng * 1e5);
    for (const delta of [iLat - prevLat, iLng - prevLng]) {
      let value = delta < 0 ? ~(delta << 1) : delta << 1;
      while (value >= 0x20) {
        out += String.fromCharCode((0x20 | (value & 0x1f)) + 63);
        value >>= 5;
      }
      out += String.fromCharCode(value + 63);
    }
    prevLat = iLat;
    prevLng = iLng;
  }
  return out;
}

function decodePolyline(encoded) {
  const points = [];
  let index = 0;
  let lat = 0;
  let lng = 0;
  while (index < encoded.length) {
    const deltas = [];
    for (let k = 0; k < 2; k++) {
      let shift = 0;
      let result = 0;
      let byte;
      do {
        byte = encoded.charCodeAt(index++) - 63;
        result |= (byte & 0x1f) << shift;
        shift += 5;
      } while (byte >= 0x20);
      deltas.push(result & 1 ? ~(result >> 1) : result >> 1);
    }
    lat += deltas[0];
    lng += deltas[1];
    points.push([lat / 1e5, lng / 1e5]);
  }
  return points;
}

function round5(x) {
  return Math.round(x * 1e5) / 1e5;
}

function addAttribution(map) {
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
}

// A ↺ button under the zoom buttons that fits the map back to its starting view.
function addResetView(map, bounds) {
  map.addControl(
    {
      onAdd() {
        const group = Object.assign(document.createElement("div"), { className: "maplibregl-ctrl maplibregl-ctrl-group" });
        const button = Object.assign(document.createElement("button"), {
          type: "button",
          className: "reset-view",
          title: "Reset zoom",
          textContent: "↺",
        });
        button.setAttribute("aria-label", "Reset zoom");
        button.addEventListener("click", () => map.fitBounds(bounds, { padding: 40 }));
        group.append(button);
        return group;
      },
      onRemove() {},
    },
    "top-right",
  );
}

function initExplore() {
  const el = document.getElementById("map");
  if (!el) return;
  const map = new maplibregl.Map({ container: el, style: el.dataset.style, center: [2.5, 46.5], zoom: 5 });
  addAttribution(map);
  let highlighted = null;
  let selected = null;
  const data = { rides: null, routes: null };
  let skipFit = false;
  let forceFit = false;
  let draw = null;
  const form = document.getElementById("filters");

  function results() {
    return document.getElementById("results");
  }

  function same(a, b) {
    return a && b && a.source === b.source && a.id === b.id;
  }

  function rowOf(target) {
    return target && document.querySelector(`#results tr[data-entity="${target.source}"][data-id="${target.id}"]`);
  }

  function featureState(target, state) {
    if (target && map.getSource(target.source)) map.setFeatureState({ source: target.source, id: target.id }, state);
  }

  function setHighlight(target, scroll) {
    if (highlighted) {
      featureState(highlighted, { hover: false });
      rowOf(highlighted)?.classList.remove("hl");
    }
    highlighted = target;
    if (!target) return;
    featureState(target, { hover: true });
    const row = rowOf(target);
    row?.classList.add("hl");
    if (scroll) row?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function featureBounds(target) {
    const feature = data[target.source]?.features.find((f) => f.id === target.id);
    if (!feature) return null;
    let [w, s, e, n] = [Infinity, Infinity, -Infinity, -Infinity];
    for (const line of feature.geometry.coordinates) {
      for (const [x, y] of line) {
        w = Math.min(w, x);
        s = Math.min(s, y);
        e = Math.max(e, x);
        n = Math.max(n, y);
      }
    }
    return Number.isFinite(w) ? [[w, s], [e, n]] : null;
  }

  // A selected ride or route stays highlighted (and the map zooms to it) while everything
  // else remains drawn, so it can be seen in context.
  function setSelected(target, zoom) {
    if (selected) {
      featureState(selected, { selected: false });
      rowOf(selected)?.classList.remove("selected");
    }
    selected = target;
    if (!target) return;
    featureState(target, { selected: true });
    rowOf(target)?.classList.add("selected");
    const bounds = zoom ? featureBounds(target) : null;
    if (bounds) map.fitBounds(bounds, { padding: 60, maxZoom: 15, duration: 600 });
  }

  function fitResults() {
    const bbox = JSON.parse(results()?.dataset.bbox || "null");
    if (bbox) map.fitBounds(boundsOf(bbox), { padding: 40, maxZoom: 14, duration: 400 });
  }

  function activeAreas() {
    const features = [];
    const bbox = document.getElementById("f-bbox")?.value;
    const area = document.getElementById("f-area")?.value;
    if (bbox) {
      const [w, s, e, n] = bbox.split(",").map(Number);
      features.push({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] } });
    }
    if (area) {
      try {
        const ring = decodePolyline(area).map(([lat, lng]) => [lng, lat]);
        if (ring.length >= 3) {
          ring.push(ring[0]);
          features.push({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } });
        }
      } catch (err) {
        console.warn("bad area", err);
      }
    }
    return { type: "FeatureCollection", features };
  }

  function applyArea(bbox, area) {
    document.getElementById("f-bbox").value = bbox || "";
    document.getElementById("f-area").value = area || "";
    skipFit = true;
    form.requestSubmit();
  }

  function stopDrawing() {
    if (draw) {
      draw.clear();
      draw.setMode("static");
    }
    document.querySelectorAll(".map-tools button").forEach((b) => b.classList.remove("active"));
  }

  async function fetchCollection(url) {
    if (!url) return { type: "FeatureCollection", features: [] };
    const response = await fetch(url);
    return response.json();
  }

  async function load(fit) {
    const section = results();
    if (!section) return;
    map.getSource("areas")?.setData(activeAreas());
    const [rides, routes, regions] = await Promise.all([
      fetchCollection(section.dataset.geojson),
      fetchCollection(section.dataset.routesGeojson),
      fetchCollection(section.dataset.regionsGeojson),
    ]);
    map.getSource("regions")?.setData(regions);
    highlighted = null;
    selected = null;
    data.rides = rides;
    data.routes = routes;
    map.getSource("rides").setData(rides);
    map.getSource("routes").setData(routes);
    const bbox = JSON.parse(section.dataset.bbox || "null");
    if (fit && (forceFit || !skipFit) && bbox) map.fitBounds(boundsOf(bbox), { padding: 40, maxZoom: 14, duration: 0 });
    skipFit = false;
    forceFit = false;
  }

  map.on("load", () => {
    map.addSource("regions", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "regions-fill", type: "fill", source: "regions", paint: { "fill-color": COLORS.region, "fill-opacity": 0.06 } });
    map.addLayer({ id: "regions-line", type: "line", source: "regions", paint: { "line-color": COLORS.region, "line-width": 1.5, "line-opacity": 0.7 } });
    map.addSource("rides", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({
      id: "rides",
      type: "line",
      source: "rides",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": ["case", ACTIVE, COLORS.highlight, ["boolean", ["get", "foot"], false], COLORS.foot, COLORS.ride],
        "line-opacity": ["case", ACTIVE, 1, 0.35],
        "line-width": ["case", ACTIVE, 4, 2],
      },
    });
    map.addSource("routes", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({
      id: "routes",
      type: "line",
      source: "routes",
      layout: { "line-join": "round" },
      paint: {
        "line-color": ["case", ACTIVE, COLORS.highlight, COLORS.route],
        "line-opacity": ["case", ACTIVE, 1, 0.8],
        "line-width": ["case", ACTIVE, 4, 2.5],
        "line-dasharray": [2, 1.5],
      },
    });
    map.addSource("areas", { type: "geojson", data: activeAreas() });
    map.addLayer({ id: "areas-fill", type: "fill", source: "areas", paint: { "fill-color": COLORS.highlight, "fill-opacity": 0.05 } });
    map.addLayer({
      id: "areas-line",
      type: "line",
      source: "areas",
      paint: { "line-color": COLORS.highlight, "line-width": 1.5, "line-dasharray": [3, 2] },
    });
    load(true);

    if (window.terraDraw && window.terraDrawMaplibreGlAdapter) {
      draw = new terraDraw.TerraDraw({
        adapter: new terraDrawMaplibreGlAdapter.TerraDrawMapLibreGLAdapter({ map }),
        modes: [new terraDraw.TerraDrawRectangleMode(), new terraDraw.TerraDrawPolygonMode()],
      });
      draw.start();
      draw.on("finish", (id) => {
        const feature = draw.getSnapshot().find((f) => f.id === id);
        const mode = draw.getMode();
        setTimeout(stopDrawing, 0);
        if (!feature) return;
        const ring = feature.geometry.coordinates[0].slice(0, -1);
        if (mode === "rectangle") {
          const xs = ring.map((c) => c[0]);
          const ys = ring.map((c) => c[1]);
          const bbox = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)].map(round5).join(",");
          applyArea(bbox, "");
        } else {
          applyArea("", encodePolyline(ring.map(([lng, lat]) => [lat, lng])));
        }
      });
    }

    document.querySelectorAll(".map-tools button").forEach((button) => {
      button.addEventListener("click", () => {
        const tool = button.dataset.tool;
        if (tool === "reset") {
          stopDrawing();
          setHighlight(null);
          setSelected(null);
          if (document.getElementById("f-bbox").value || document.getElementById("f-area").value) {
            forceFit = true;
            applyArea("", "");
          } else {
            fitResults();
          }
          return;
        }
        if (tool === "view") {
          stopDrawing();
          const b = map.getBounds();
          const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
            .map((v, i) => Math.max(i % 2 ? -90 : -180, Math.min(i % 2 ? 90 : 180, v)))
            .map(round5)
            .join(",");
          applyArea(bbox, "");
          return;
        }
        if (!draw) return;
        const active = button.classList.contains("active");
        stopDrawing();
        if (!active) {
          button.classList.add("active");
          draw.setMode(tool);
        }
      });
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") stopDrawing();
    });

    for (const source of SOURCES) {
      map.on("mousemove", source, (e) => {
        map.getCanvas().style.cursor = "pointer";
        const id = e.features[0]?.id;
        if (id !== undefined && !same(highlighted, { source, id })) setHighlight({ source, id }, false);
      });
      map.on("mouseleave", source, () => {
        map.getCanvas().style.cursor = "";
        setHighlight(null);
      });
      map.on("click", source, (e) => {
        if (draw && draw.getMode() !== "static") return;
        const id = e.features[0]?.id;
        if (id !== undefined) {
          setHighlight({ source, id }, true);
          setSelected({ source, id }, false);
        }
      });
      map.on("dblclick", source, (e) => {
        const id = e.features[0]?.id;
        if (id !== undefined) {
          e.preventDefault();
          window.location.href = `/${source}/${id}`;
        }
      });
    }
  });

  document.addEventListener("click", (e) => {
    if (e.target.closest?.("[data-action='clear-area']")) {
      applyArea("", "");
      return;
    }
    const row = e.target.closest?.("#results tr[data-id]");
    if (!row || e.target.closest("a, button, input, label, .col-pick") || !map.getSource("rides")) return;
    const target = { source: row.dataset.entity, id: Number(row.dataset.id) };
    setSelected(same(selected, target) ? null : target, !same(selected, target));
  });

  document.addEventListener("mouseover", (e) => {
    const row = e.target.closest?.("#results tr[data-id]");
    if (row && map.getSource("rides")) setHighlight({ source: row.dataset.entity, id: Number(row.dataset.id) }, false);
  });

  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.detail.target.id === "results" || e.detail.elt?.id === "results") {
      if (map.getSource("rides")) load(true);
    }
  });
}

function initRide() {
  const el = document.getElementById("ride-map");
  if (!el) return;
  const track = JSON.parse(el.dataset.track);
  const map = new maplibregl.Map({ container: el, style: el.dataset.style, center: [2.5, 46.5], zoom: 5 });
  addAttribution(map);
  const bounds = lineBounds([track]);
  map.fitBounds(bounds, { padding: 40, duration: 0 });
  addResetView(map, bounds);
  map.on("load", () => {
    map.addSource("routes", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({
      id: "routes",
      type: "line",
      source: "routes",
      layout: { "line-join": "round", visibility: "none" },
      paint: {
        "line-color": COLORS.route,
        "line-width": 3,
        "line-dasharray": [2, 1.5],
        "line-opacity": HOVER_OPACITY(0.6),
      },
    });
    linkPopups(map, "routes", "routes");
    map.addSource("track", { type: "geojson", data: track });
    map.addLayer({
      id: "track",
      type: "line",
      source: "track",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": el.dataset.foot ? COLORS.foot : COLORS.ride, "line-width": 4 },
    });
    addRiddenLayer(map);
    addProfileLayers(map, "selection");
    const first = track.coordinates[0][0];
    const last = track.coordinates.at(-1).at(-1);
    new maplibregl.Marker({ color: "#2f7d32", scale: 0.7 }).setLngLat(first).addTo(map);
    new maplibregl.Marker({ color: COLORS.highlight, scale: 0.7 }).setLngLat(last).addTo(map);
    addProfileLayers(map, "cursor");
  });
  initPairRows(map, initProfile(map, track, "track"));
  initLayerToggles(map, "ride");
  document.body.addEventListener("syncFinished", () => window.location.reload());
}

const EMPTY = { type: "FeatureCollection", features: [] };

// The elevation chart's section highlight (drawn above the line) and hover dot (on top).
function addProfileLayers(map, which) {
  map.addSource(which, { type: "geojson", data: EMPTY });
  if (which === "selection") {
    map.addLayer({
      id: "selection",
      type: "line",
      source: "selection",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": COLORS.selection, "line-width": 7 },
    });
  } else {
    map.addLayer({
      id: "cursor",
      type: "circle",
      source: "cursor",
      paint: { "circle-radius": 6, "circle-color": COLORS.highlight, "circle-stroke-color": "#fff", "circle-stroke-width": 2 },
    });
  }
}

function niceStep(range, count) {
  const raw = range / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw);
}

// Same rule as geo.elevation_gain: a climb counts once it rises 10 m above the last low and
// ends with a 10 m drop, so noise doesn't add up. Loss is the gain of the reversed section.
function elevationGain(alts, threshold = 10) {
  let total = 0;
  let low = alts[0];
  let high = low;
  let climbing = false;
  for (const value of alts) {
    if (climbing) {
      if (value > high) high = value;
      else if (high - value >= threshold) {
        total += high - low;
        [low, high, climbing] = [value, value, false];
      }
    } else if (value < low) low = high = value;
    else if (value - low >= threshold) [climbing, high] = [true, value];
  }
  return total + (climbing ? high - low : 0);
}

function haversineKm([lng1, lat1], [lng2, lat2]) {
  const rad = Math.PI / 180;
  const h =
    Math.sin(((lat2 - lat1) * rad) / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(((lng2 - lng1) * rad) / 2) ** 2;
  return 2 * 6371.0088 * Math.asin(Math.sqrt(h));
}

// The part of a MultiLineString between two distances (km from its start), cutting the
// end segments so the highlight starts and stops where the chart selection does.
function sliceLine(lines, from, to) {
  const pieces = [];
  let done = 0;
  for (const line of lines) {
    let piece = [];
    for (let i = 1; i < line.length; i++) {
      const [a, b] = [line[i - 1], line[i]];
      const len = haversineKm(a, b);
      const [start, end] = [done, done + len];
      done = end;
      if (end < from || start > to || !len) continue;
      const at = (d) => {
        const t = Math.min(1, Math.max(0, (d - start) / len));
        return [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
      };
      if (!piece.length) piece.push(at(from));
      piece.push(at(Math.min(to, end)));
    }
    if (piece.length > 1) pieces.push(piece);
  }
  return { type: "MultiLineString", coordinates: pieces };
}

function lineLengthKm(lines) {
  let total = 0;
  for (const line of lines) for (let i = 1; i < line.length; i++) total += haversineKm(line[i - 1], line[i]);
  return total;
}

// Runs [from, to] of profile points lying within `metres` of a line (the yellow section on
// the map), found with a coarse grid of the line's segments.
function runsNear(points, geometry, metres = 40) {
  const lines = geometry.type === "MultiLineString" ? geometry.coordinates : [geometry.coordinates];
  const cell = 0.002;
  const pad = metres / 70000;
  const grid = new Map();
  for (const line of lines) {
    for (let i = 1; i < line.length; i++) {
      const [a, b] = [line[i - 1], line[i]];
      for (let cx = Math.floor((Math.min(a[0], b[0]) - pad) / cell); cx <= Math.floor((Math.max(a[0], b[0]) + pad) / cell); cx++) {
        for (let cy = Math.floor((Math.min(a[1], b[1]) - pad) / cell); cy <= Math.floor((Math.max(a[1], b[1]) + pad) / cell); cy++) {
          const k = `${cx},${cy}`;
          if (!grid.has(k)) grid.set(k, []);
          grid.get(k).push([a, b]);
        }
      }
    }
  }
  const near = ([, , , lng, lat]) => {
    const kx = 111320 * Math.cos((lat * Math.PI) / 180);
    const ky = 110540;
    for (const [a, b] of grid.get(`${Math.floor(lng / cell)},${Math.floor(lat / cell)}`) || []) {
      const [ax, ay, bx, by] = [(a[0] - lng) * kx, (a[1] - lat) * ky, (b[0] - lng) * kx, (b[1] - lat) * ky];
      const [dx, dy] = [bx - ax, by - ay];
      const t = dx || dy ? Math.min(1, Math.max(0, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) : 0;
      if (Math.hypot(ax + t * dx, ay + t * dy) <= metres) return true;
    }
    return false;
  };
  const runs = [];
  points.forEach((p, i) => {
    if (!near(p)) return;
    const last = runs.at(-1);
    if (last && i - last[1] <= 3) last[1] = i;
    else runs.push([i, i]);
  });
  return runs;
}

function initProfile(map, track, lineLayer) {
  const el = document.getElementById("ride-profile");
  if (!el) return;
  const points = JSON.parse(el.dataset.profile); // [km, altitude, grade %, lng, lat]
  const NS = "http://www.w3.org/2000/svg";
  const pad = { left: 46, right: 10, top: 24, bottom: 18 };
  const km0 = points[0][0];
  const km1 = points.at(-1)[0];
  const alts = points.map((p) => p[1]);
  const low = Math.min(...alts);
  const span = Math.max(Math.max(...alts) - low, 60);
  const altStep = niceStep(span, 3);
  const altMin = Math.floor(low / altStep) * altStep;
  const altMax = Math.ceil((low + span) / altStep) * altStep;
  const tip = Object.assign(document.createElement("div"), { className: "profile-tip", hidden: true });
  const info = Object.assign(document.createElement("div"), { className: "profile-selection small" });
  const hint = "Drag across the chart to measure a section.";
  // Stream distances and the drawn line's length differ a little; scale between them.
  const trackScale = lineLengthKm(track.coordinates) / (km1 - km0 || 1);
  let svg, cursor, dot, band, marks, x, y;
  let selection = null; // [from index, to index]
  let marked = []; // runs highlighted from outside (a row of the ride/route table)
  let dragFrom = null;

  function node(name, attrs, parent = svg) {
    const n = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return parent.appendChild(n);
  }

  function draw() {
    const width = el.clientWidth;
    const height = el.clientHeight;
    if (!width) return;
    x = (km) => pad.left + ((km - km0) / (km1 - km0)) * (width - pad.left - pad.right);
    y = (alt) => pad.top + ((altMax - alt) / (altMax - altMin)) * (height - pad.top - pad.bottom);
    svg?.remove();
    svg = document.createElementNS(NS, "svg");
    el.prepend(svg);
    for (let alt = altMin; alt <= altMax; alt += altStep) {
      node("line", { class: "grid", x1: pad.left, x2: width - pad.right, y1: y(alt), y2: y(alt) });
      node("text", { class: "axis", x: pad.left - 6, y: y(alt) + 4, "text-anchor": "end" }).textContent = `${alt} m`;
    }
    const kmStep = niceStep(km1 - km0, Math.max(2, Math.floor(width / 90)));
    for (let km = Math.ceil(km0 / kmStep) * kmStep; km <= km1; km += kmStep) {
      node("text", { class: "axis", x: x(km), y: height - 3, "text-anchor": "middle" }).textContent = `${km} km`;
    }
    const line = points.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
    const bottom = y(altMin);
    node("path", { class: "area", d: `${line}L${x(km1)},${bottom}L${x(km0)},${bottom}Z` });
    node("path", { class: "line", d: line });
    marks = node("g", { class: "marks" });
    band = node("rect", { class: "band", y: pad.top - 4, height: bottom - pad.top + 4, visibility: "hidden" });
    cursor = node("line", { class: "cursor", y1: pad.top - 4, y2: bottom, visibility: "hidden" });
    dot = node("circle", { class: "dot", r: 4, visibility: "hidden" });
    drawBand();
    drawMarks();
  }

  function drawMarks() {
    if (!marks) return;
    const bottom = y(altMin);
    marks.replaceChildren();
    for (const [a, b] of marked) {
      const [x0, x1] = [x(points[a][0]), x(points[b][0])];
      node("rect", { x: x0, width: Math.max(x1 - x0, 2), y: pad.top - 4, height: bottom - pad.top + 4 }, marks);
    }
  }

  function drawBand() {
    if (!band) return;
    if (!selection) return band.setAttribute("visibility", "hidden");
    const [a, b] = selection.map((i) => x(points[i][0]));
    band.setAttribute("x", Math.min(a, b));
    band.setAttribute("width", Math.abs(b - a));
    band.setAttribute("visibility", "visible");
  }

  function select(range) {
    selection = range && Math.abs(range[1] - range[0]) >= 2 ? [...range].sort((a, b) => a - b) : null;
    drawBand();
    if (!selection) {
      info.replaceChildren(hint);
      map.getSource("selection")?.setData({ type: "FeatureCollection", features: [] });
      return;
    }
    const part = points.slice(selection[0], selection[1] + 1);
    const [from, to] = [part[0][0], part.at(-1)[0]];
    const alts = part.map((p) => p[1]);
    const up = elevationGain(alts);
    const down = elevationGain([...alts].reverse());
    const grade = ((alts.at(-1) - alts[0]) / ((to - from) * 1000)) * 100;
    const stat = (text) => Object.assign(document.createElement("strong"), { textContent: text });
    const button = (text, action) => {
      const b = Object.assign(document.createElement("button"), { type: "button", className: "link-button", textContent: text });
      b.addEventListener("click", action);
      return b;
    };
    const line = sliceLine(track.coordinates, (from - km0) * trackScale, (to - km0) * trackScale);
    info.replaceChildren(
      stat(`${(to - from).toFixed(2)} km`), " · ↗ ", stat(`${Math.round(up)} m`), " ↘ ", stat(`${Math.round(down)} m`),
      ` · avg ${grade > 0 ? "+" : ""}${grade.toFixed(1)} % · km ${from.toFixed(1)}–${to.toFixed(1)} `,
      button("Zoom map", () => {
        const bounds = lineBounds([line]);
        if (bounds) map.fitBounds(bounds, { padding: 40 });
      }),
      " ",
      button("Clear", () => select(null)),
    );
    map.getSource("selection")?.setData(line);
  }

  function show(i) {
    const [km, alt, grade, lng, lat] = points[i];
    const px = x(km);
    for (const [n, attrs] of [[cursor, { x1: px, x2: px }], [dot, { cx: px, cy: y(alt) }]]) {
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      n.setAttribute("visibility", "visible");
    }
    tip.hidden = false;
    tip.textContent = `${km.toFixed(1)} km · ${Math.round(alt)} m · ${grade > 0 ? "+" : ""}${grade.toFixed(1)} %`;
    const half = tip.offsetWidth / 2;
    tip.style.left = `${Math.min(Math.max(px, half), el.clientWidth - half)}px`;
    map.getSource("cursor")?.setData({ type: "Point", coordinates: [lng, lat] });
  }

  function hide() {
    cursor?.setAttribute("visibility", "hidden");
    dot?.setAttribute("visibility", "hidden");
    tip.hidden = true;
    map.getSource("cursor")?.setData({ type: "FeatureCollection", features: [] });
  }

  function indexAt(km) {
    let lo = 0;
    let hi = points.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (points[mid][0] < km) lo = mid + 1;
      else hi = mid;
    }
    return lo > 0 && km - points[lo - 1][0] < points[lo][0] - km ? lo - 1 : lo;
  }

  const indexAtPointer = (e) => {
    const px = e.clientX - el.getBoundingClientRect().left;
    const width = el.clientWidth - pad.left - pad.right;
    return indexAt(km0 + ((px - pad.left) / width) * (km1 - km0));
  };

  el.append(tip);
  el.after(info);
  info.replaceChildren(hint);
  draw();
  new ResizeObserver(draw).observe(el);
  el.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    el.setPointerCapture(e.pointerId);
    dragFrom = indexAtPointer(e);
  });
  el.addEventListener("pointermove", (e) => {
    const i = indexAtPointer(e);
    show(i);
    if (dragFrom === null) return;
    selection = [Math.min(dragFrom, i), Math.max(dragFrom, i)];
    drawBand();
  });
  const endDrag = (e) => {
    if (dragFrom === null) return;
    const range = [dragFrom, indexAtPointer(e)];
    dragFrom = null;
    select(range);
  };
  el.addEventListener("pointerup", endDrag);
  el.addEventListener("pointercancel", () => {
    dragFrom = null;
    select(selection);
  });
  el.addEventListener("pointerleave", () => dragFrom === null && hide());
  document.addEventListener("keydown", (e) => e.key === "Escape" && selection && select(null));
  map.on("mousemove", lineLayer, (e) => {
    const { lng, lat } = e.lngLat;
    const k = Math.cos((lat * Math.PI) / 180) ** 2;
    let best = 0;
    let bestD = Infinity;
    points.forEach((p, i) => {
      const d = (p[3] - lng) ** 2 * k + (p[4] - lat) ** 2;
      if (d < bestD) [best, bestD] = [i, d];
    });
    show(best);
  });
  map.on("mouseleave", lineLayer, hide);
  return {
    mark(geometry) {
      marked = geometry ? runsNear(points, geometry) : [];
      drawMarks();
    },
  };
}

const RESIZE_KEY = "velotheque.resize.";

function storage(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    if (value === null) return localStorage.removeItem(key);
    localStorage.setItem(key, value);
  } catch {
    return null;
  }
}

function initResizers() {
  const explore = document.querySelector(".explore");
  const share = storage(`${RESIZE_KEY}explore`);
  if (explore && share) explore.style.setProperty("--map-share", share);
  document.querySelectorAll("[data-resize]").forEach((handle) => {
    const target = document.getElementById(handle.dataset.resize);
    const height = storage(`${RESIZE_KEY}${handle.dataset.resize}`);
    if (target && height) target.style.height = height;
  });

  function resize(handle, clientY, delta) {
    if (handle.dataset.resize === "explore-split") {
      const box = explore.getBoundingClientRect();
      const current = parseFloat(getComputedStyle(explore).getPropertyValue("--map-share")) || 55;
      const pct = clientY === null ? current + (delta / box.height) * 100 : ((clientY - box.top) / box.height) * 100;
      const value = `${Math.min(85, Math.max(15, pct)).toFixed(1)}%`;
      explore.style.setProperty("--map-share", value);
      return ["explore", value];
    }
    const target = document.getElementById(handle.dataset.resize);
    const top = target.getBoundingClientRect().top;
    const px = clientY === null ? target.offsetHeight + delta : clientY - top;
    const value = `${Math.round(Math.min(window.innerHeight * 0.9, Math.max(180, px)))}px`;
    target.style.height = value;
    return [handle.dataset.resize, value];
  }

  document.addEventListener("pointerdown", (e) => {
    const handle = e.target.closest?.("[data-resize]");
    if (!handle) return;
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("dragging");
    document.body.classList.add("dragging-resize");
    let last;
    const move = (ev) => (last = resize(handle, ev.clientY));
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.classList.remove("dragging");
      document.body.classList.remove("dragging-resize");
      if (last) storage(RESIZE_KEY + last[0], last[1]);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up, { once: true });
    handle.addEventListener("pointercancel", up, { once: true });
  });
  document.addEventListener("dblclick", (e) => {
    const handle = e.target.closest?.("[data-resize]");
    if (!handle) return;
    if (handle.dataset.resize === "explore-split") explore.style.removeProperty("--map-share");
    else document.getElementById(handle.dataset.resize).style.removeProperty("height");
    storage(`${RESIZE_KEY}${handle.dataset.resize === "explore-split" ? "explore" : handle.dataset.resize}`, null);
  });
  document.addEventListener("keydown", (e) => {
    const handle = e.target.closest?.("[data-resize]");
    if (!handle || !["ArrowUp", "ArrowDown"].includes(e.key)) return;
    e.preventDefault();
    const [key, value] = resize(handle, null, e.key === "ArrowUp" ? -40 : 40);
    storage(RESIZE_KEY + key, value);
  });
}

function lineBounds(collections) {
  let [w, s, e, n] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const geometry of collections) {
    const lines = geometry.type === "MultiLineString" ? geometry.coordinates : [geometry.coordinates];
    for (const line of lines) {
      for (const [x, y] of line) {
        w = Math.min(w, x);
        s = Math.min(s, y);
        e = Math.max(e, x);
        n = Math.max(n, y);
      }
    }
  }
  return Number.isFinite(w) ? [[w, s], [e, n]] : null;
}

function initRoute() {
  const el = document.getElementById("route-map");
  if (!el) return;
  const line = JSON.parse(el.dataset.line);
  const covered = JSON.parse(el.dataset.covered);
  const rides = JSON.parse(el.dataset.rides);
  const map = new maplibregl.Map({ container: el, style: el.dataset.style, center: [2.5, 46.5], zoom: 5 });
  addAttribution(map);
  const bounds = lineBounds([line]);
  if (bounds) {
    map.fitBounds(bounds, { padding: 40, duration: 0 });
    addResetView(map, bounds);
  }
  map.on("load", () => {
    map.addSource("rides", { type: "geojson", data: rides });
    map.addLayer({
      id: "rides",
      type: "line",
      source: "rides",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": COLORS.ride,
        "line-opacity": HOVER_OPACITY(0.35),
        "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 3, 2],
      },
    });
    map.addSource("route", { type: "geojson", data: line });
    map.addLayer({
      id: "route",
      type: "line",
      source: "route",
      layout: { "line-join": "round" },
      paint: { "line-color": COLORS.route, "line-width": 3, "line-dasharray": [2, 1.5] },
    });
    if (covered) {
      map.addSource("covered", { type: "geojson", data: covered });
      map.addLayer({
        id: "covered",
        type: "line",
        source: "covered",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": COLORS.covered, "line-width": 5 },
      });
    }
    addRiddenLayer(map);
    addProfileLayers(map, "selection");
    addProfileLayers(map, "cursor");
    linkPopups(map, "rides", "rides");
  });
  initPairRows(map, initProfile(map, line, "route"));
  initLayerToggles(map, "route");
}

// The ride ↔ route tables (rides on a route, routes of a ride). Hovering a row previews the
// part of the route the ride covered (fetched once): yellow on the map and on the elevation
// chart, with the other line brought forward. Clicking a row (anywhere but the name) selects
// it like in Explore: it stays highlighted and the map zooms to it, until the row is clicked
// again, Esc, or a click elsewhere on the page (the map and chart excepted).
function initPairRows(map, profile) {
  const table = document.querySelector("table.pairs");
  if (!table) return;
  const source = table.dataset.source;
  const cache = new Map();
  let shown = null;
  let selected = null;
  const piece = (row) => {
    if (!cache.has(row.dataset.piece)) {
      cache.set(row.dataset.piece, fetch(row.dataset.piece).then((r) => (r.ok ? r.json() : null)));
    }
    return cache.get(row.dataset.piece);
  };
  const show = async (row) => {
    if (row === shown) return;
    if (shown) map.setFeatureState({ source, id: Number(shown.dataset.id) }, { hover: false });
    shown = row;
    if (!row) {
      map.getSource("ridden")?.setData(EMPTY);
      profile?.mark(null);
      return;
    }
    map.setFeatureState({ source, id: Number(row.dataset.id) }, { hover: true });
    const geometry = await piece(row);
    if (shown !== row || !geometry) return;
    map.getSource("ridden")?.setData(geometry);
    profile?.mark(geometry);
  };
  const select = async (row) => {
    selected?.classList.remove("selected");
    selected = row;
    row?.classList.add("selected");
    show(row);
    if (!row) return;
    const geometry = await piece(row);
    const bounds = geometry && lineBounds([geometry]);
    if (bounds && selected === row) map.fitBounds(bounds, { padding: 60, maxZoom: 15 });
    map.getContainer().scrollIntoView({ behavior: "smooth", block: "nearest" });
  };
  table.addEventListener("mouseover", (e) => {
    const row = e.target.closest("tr[data-piece]");
    if (row) show(row);
  });
  table.addEventListener("mouseleave", () => show(selected));
  document.addEventListener("click", (e) => {
    const row = e.target.closest?.("table.pairs tr[data-piece]");
    if (row) {
      if (!e.target.closest("a")) select(row === selected ? null : row);
    } else if (selected && !e.target.closest?.(".ride-map, .profile, .profile-selection, .resize-handle, .legend")) {
      select(null);
    }
  });
  document.addEventListener("keydown", (e) => e.key === "Escape" && selected && select(null));
}

function addRiddenLayer(map) {
  map.addSource("ridden", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "ridden",
    type: "line",
    source: "ridden",
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": COLORS.selection, "line-width": 7 },
  });
}

const HOVER_OPACITY = (base) => ["case", ["boolean", ["feature-state", "hover"], false], 0.95, base];

function linkPopups(map, layer, path) {
  map.on("mousemove", layer, () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
  map.on("click", layer, (e) => {
    const feature = e.features[0];
    if (!feature) return;
    new maplibregl.Popup({ closeButton: false })
      .setLngLat(e.lngLat)
      .setHTML(`<a href="/${path}/${feature.id}"></a>`)
      .addTo(map)
      .getElement()
      .querySelector("a").textContent = feature.properties.name || "Untitled";
  });
}

// Checkboxes under a map that show or hide one of its layers; the choice is remembered per
// page kind, and a layer with data-url is fetched the first time it is shown.
function initLayerToggles(map, page) {
  document.querySelectorAll("input[data-layer]").forEach((input) => {
    const layer = input.dataset.layer;
    const key = `velotheque.layer.${page}.${layer}`;
    const saved = storage(key);
    if (saved !== null) input.checked = saved === "1";
    let fetched = !input.dataset.url;
    const apply = async () => {
      if (input.checked && !fetched) {
        fetched = true;
        const response = await fetch(input.dataset.url);
        if (response.ok) map.getSource(layer)?.setData(await response.json());
      }
      map.setLayoutProperty(layer, "visibility", input.checked ? "visible" : "none");
    };
    input.addEventListener("change", () => {
      storage(key, input.checked ? "1" : "0");
      apply();
    });
    if (map.loaded()) apply();
    else map.once("load", apply);
  });
}

function initTagSuggestions() {
  const input = document.getElementById("route-tags");
  if (!input) return;
  document.querySelectorAll("[data-add-tag]").forEach((button) =>
    button.addEventListener("click", () => {
      const tags = input.value.split(",").map((t) => t.trim()).filter(Boolean);
      if (!tags.some((t) => t.toLowerCase() === button.dataset.addTag.toLowerCase())) tags.push(button.dataset.addTag);
      input.value = tags.join(", ");
      button.remove();
    }),
  );
}

function initBulkEdit() {
  const picks = () => [...document.querySelectorAll("#results input[name='ids']")];
  const refresh = () => {
    const form = document.getElementById("bulk");
    if (!form) return;
    const n = picks().filter((c) => c.checked).length;
    form.hidden = n === 0;
    form.querySelector(".bulk-count").textContent = `${n} route${n === 1 ? "" : "s"} selected`;
    const all = document.querySelector("#results [data-action='bulk-all']");
    if (all) {
      all.checked = n > 0 && n === picks().length;
      all.indeterminate = n > 0 && n < picks().length;
    }
  };
  document.addEventListener("change", (e) => {
    if (e.target.matches?.("#results [data-action='bulk-all']")) {
      picks().forEach((c) => (c.checked = e.target.checked));
      refresh();
    } else if (e.target.matches?.("#results input[name='ids']")) refresh();
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest?.("[data-action='bulk-clear']")) return;
    picks().forEach((c) => (c.checked = false));
    refresh();
  });
}

// Collapsible sections keep the reader's last choice (per section kind, not per page).
function initCollapsibles() {
  document.querySelectorAll("details[data-remember]").forEach((details) => {
    const key = `velotheque.open.${details.dataset.remember}`;
    const saved = storage(key);
    if (saved !== null) details.open = saved === "1";
    details.addEventListener("toggle", () => storage(key, details.open ? "1" : "0"));
  });
}

function initLightbox() {
  const dialog = document.getElementById("lightbox");
  if (!dialog) return;
  const links = [...document.querySelectorAll("[data-lightbox]")];
  const image = dialog.querySelector("img");
  const video = dialog.querySelector("video");
  let current = 0;
  function show(i) {
    current = (i + links.length) % links.length;
    const link = links[current];
    const isVideo = link.dataset.lightbox === "video";
    video.pause();
    image.hidden = isVideo;
    video.hidden = !isVideo;
    if (isVideo) {
      image.removeAttribute("src");
      video.src = link.href;
      video.play().catch(() => {});
    } else {
      video.removeAttribute("src");
      image.src = link.href;
      image.alt = link.querySelector("img")?.alt || "";
    }
  }
  links.forEach((link, i) =>
    link.addEventListener("click", (e) => {
      e.preventDefault();
      show(i);
      dialog.showModal();
    }),
  );
  dialog.addEventListener("click", (e) => {
    const step = e.target.closest("[data-step]");
    if (step) show(current + Number(step.dataset.step));
    else if (e.target === dialog || e.target === image) dialog.close();
  });
  dialog.addEventListener("close", () => video.pause());
  dialog.addEventListener("keydown", (e) => {
    if (e.target === video) return;
    if (e.key === "ArrowRight") show(current + 1);
    if (e.key === "ArrowLeft") show(current - 1);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  // Saved map sizes first: MapLibre ignores the first resize it observes, so a container
  // resized after the map is created would keep the default-size canvas.
  initResizers();
  initExplore();
  initRide();
  initRoute();
  initTagSuggestions();
  initBulkEdit();
  initCollapsibles();
  initLightbox();
});
