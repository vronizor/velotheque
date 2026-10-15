"use strict";

const COLORS = { ride: "#D63A2A", highlight: "#161412" };

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
  let skipFit = false;
  let draw = null;
  const form = document.getElementById("filters");

  function results() {
    return document.getElementById("results");
  }

  function setHighlight(id, scroll) {
    if (highlighted !== null) {
      map.setFeatureState({ source: "rides", id: highlighted }, { hover: false });
      document.querySelector(`#results tr[data-id="${highlighted}"]`)?.classList.remove("hl");
    }
    highlighted = id;
    if (id === null) return;
    map.setFeatureState({ source: "rides", id }, { hover: true });
    const row = document.querySelector(`#results tr[data-id="${id}"]`);
    row?.classList.add("hl");
    if (scroll) row?.scrollIntoView({ block: "nearest", behavior: "smooth" });
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

  async function load(fit) {
    const section = results();
    if (!section) return;
    map.getSource("areas")?.setData(activeAreas());
    const response = await fetch(section.dataset.geojson);
    const data = await response.json();
    highlighted = null;
    map.getSource("rides").setData(data);
    const bbox = JSON.parse(section.dataset.bbox || "null");
    if (fit && !skipFit && bbox) map.fitBounds(boundsOf(bbox), { padding: 40, maxZoom: 14, duration: 0 });
    skipFit = false;
  }

  map.on("load", () => {
    map.addSource("rides", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({
      id: "rides",
      type: "line",
      source: "rides",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": ["case", ["boolean", ["feature-state", "hover"], false], COLORS.highlight, COLORS.ride],
        "line-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 1, 0.35],
        "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 4, 2],
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

    map.on("mousemove", "rides", (e) => {
      map.getCanvas().style.cursor = "pointer";
      const id = e.features[0]?.id;
      if (id !== undefined && id !== highlighted) setHighlight(id, false);
    });
    map.on("mouseleave", "rides", () => {
      map.getCanvas().style.cursor = "";
      setHighlight(null);
    });
    map.on("click", "rides", (e) => {
      if (draw && draw.getMode() !== "static") return;
      const id = e.features[0]?.id;
      if (id !== undefined) setHighlight(id, true);
    });
    map.on("dblclick", "rides", (e) => {
      const id = e.features[0]?.id;
      if (id !== undefined) {
        e.preventDefault();
        window.location.href = `/rides/${id}`;
      }
    });
  });

  document.addEventListener("click", (e) => {
    if (e.target.closest?.("[data-action='clear-area']")) applyArea("", "");
  });

  document.addEventListener("mouseover", (e) => {
    const row = e.target.closest?.("#results tr[data-id]");
    if (row && map.getSource("rides")) setHighlight(Number(row.dataset.id), false);
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
  });
  document.body.addEventListener("syncFinished", () => window.location.reload());
}

function initLightbox() {
  const dialog = document.getElementById("lightbox");
  if (!dialog) return;
  const links = [...document.querySelectorAll("[data-lightbox]")];
  const image = dialog.querySelector("img");
  let current = 0;
  function show(i) {
    current = (i + links.length) % links.length;
    image.src = links[current].href;
    image.alt = links[current].querySelector("img")?.alt || "";
  }
  links.forEach((link, i) =>
    link.addEventListener("click", (e) => {
      e.preventDefault();
      show(i);
      dialog.showModal();
    }),
  );
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog || e.target === image) dialog.close();
  });
  dialog.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") show(current + 1);
    if (e.key === "ArrowLeft") show(current - 1);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initExplore();
  initRide();
  initLightbox();
});
