"use strict";

const COLORS = { ride: "#D63A2A", route: "#1F5FAD", covered: "#0B3C7A", highlight: "#161412" };
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
    const [rides, routes] = await Promise.all([
      fetchCollection(section.dataset.geojson),
      fetchCollection(section.dataset.routesGeojson),
    ]);
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
    map.addSource("rides", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({
      id: "rides",
      type: "line",
      source: "rides",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": ["case", ACTIVE, COLORS.highlight, COLORS.ride],
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
  const coords = track.coordinates.flat();
  const xs = coords.map((c) => c[0]);
  const ys = coords.map((c) => c[1]);
  map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]], {
    padding: 40,
    duration: 0,
  });
  map.on("load", () => {
    map.addSource("routes", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({
      id: "routes",
      type: "line",
      source: "routes",
      layout: { "line-join": "round", visibility: "none" },
      paint: { "line-color": COLORS.route, "line-width": 3, "line-dasharray": [2, 1.5] },
    });
    linkPopups(map, "routes", "routes");
    map.addSource("track", { type: "geojson", data: track });
    map.addLayer({
      id: "track",
      type: "line",
      source: "track",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": COLORS.ride, "line-width": 4 },
    });
    const first = track.coordinates[0][0];
    const last = track.coordinates.at(-1).at(-1);
    new maplibregl.Marker({ color: "#2f7d32", scale: 0.7 }).setLngLat(first).addTo(map);
    new maplibregl.Marker({ color: COLORS.highlight, scale: 0.7 }).setLngLat(last).addTo(map);
    map.addSource("cursor", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({
      id: "cursor",
      type: "circle",
      source: "cursor",
      paint: { "circle-radius": 6, "circle-color": COLORS.highlight, "circle-stroke-color": "#fff", "circle-stroke-width": 2 },
    });
  });
  initProfile(map);
  initLayerToggles(map, "ride");
  document.body.addEventListener("syncFinished", () => window.location.reload());
}

function niceStep(range, count) {
  const raw = range / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw);
}

function initProfile(map) {
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
  let svg, cursor, dot, x, y;

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
    cursor = node("line", { class: "cursor", y1: pad.top - 4, y2: bottom, visibility: "hidden" });
    dot = node("circle", { class: "dot", r: 4, visibility: "hidden" });
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

  el.append(tip);
  draw();
  new ResizeObserver(draw).observe(el);
  el.addEventListener("pointermove", (e) => {
    const px = e.clientX - el.getBoundingClientRect().left;
    const width = el.clientWidth - pad.left - pad.right;
    show(indexAt(km0 + ((px - pad.left) / width) * (km1 - km0)));
  });
  el.addEventListener("pointerleave", hide);
  map.on("mousemove", "track", (e) => {
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
  map.on("mouseleave", "track", hide);
}

const RESIZE_KEY = "velotheque.resize.";

function storage(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
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
  if (bounds) map.fitBounds(bounds, { padding: 40, duration: 0 });
  map.on("load", () => {
    map.addSource("rides", { type: "geojson", data: rides });
    map.addLayer({
      id: "rides",
      type: "line",
      source: "rides",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": COLORS.ride, "line-opacity": 0.35, "line-width": 2 },
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
    linkPopups(map, "rides", "rides");
  });
  initLayerToggles(map, "route");
}

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
  initExplore();
  initRide();
  initRoute();
  initTagSuggestions();
  initBulkEdit();
  initResizers();
  initLightbox();
});
