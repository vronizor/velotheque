"use strict";

const COLORS = { ride: "#D63A2A", highlight: "#161412" };

function boundsOf(bbox) {
  return [[bbox[0], bbox[1]], [bbox[2], bbox[3]]];
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

  async function load(fit) {
    const section = results();
    if (!section) return;
    const response = await fetch(section.dataset.geojson);
    const data = await response.json();
    highlighted = null;
    map.getSource("rides").setData(data);
    const bbox = JSON.parse(section.dataset.bbox || "null");
    if (fit && bbox) map.fitBounds(boundsOf(bbox), { padding: 40, maxZoom: 14, duration: 0 });
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
    load(true);

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

document.addEventListener("DOMContentLoaded", () => {
  initExplore();
  initRide();
});
