"""GPX out: a ride's track as GPX 1.1, and links that open files in gpx.studio."""

import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from urllib.parse import quote

GPX_STUDIO = "https://gpx.studio"
NS = "http://www.topografix.com/GPX/1/1"


def slug(name: str | None, fallback: str = "track") -> str:
    ascii_name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-").lower()[:60].rstrip("-")
    return cleaned or fallback


def file_slug(filename: str) -> str:
    """A URL-safe file name that keeps the extension (gpx.studio reads the format from it)."""
    path = PurePosixPath(filename)
    suffix = re.sub(r"[^A-Za-z0-9.]", "", path.suffix).lower() or ".gpx"
    return slug(path.stem, "route") + suffix


def gpx_studio_url(file_urls: list[str]) -> str:
    """gpx.studio's own "Open in" link: the browser fetches each file (CORS needed)."""
    return f"{GPX_STUDIO}/app?files={quote(json.dumps(file_urls), safe='')}"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ride_gpx(
    name: str,
    kind: str,
    start_at: datetime,
    lines: list[list[list[float]]],
    time_s: list[int] | None,
    altitude_m: list[float | None] | None,
) -> bytes:
    """One <trkseg> per piece of the track (gaps stay gaps); time and elevation per point when
    the track has them. Coordinates are GeoJSON [lng, lat]; the arrays run over all pieces."""
    ET.register_namespace("", NS)
    root = ET.Element(f"{{{NS}}}gpx", version="1.1", creator="Vélothèque")
    metadata = ET.SubElement(root, f"{{{NS}}}metadata")
    ET.SubElement(metadata, f"{{{NS}}}name").text = name
    ET.SubElement(metadata, f"{{{NS}}}time").text = _iso(start_at)
    trk = ET.SubElement(root, f"{{{NS}}}trk")
    ET.SubElement(trk, f"{{{NS}}}name").text = name
    ET.SubElement(trk, f"{{{NS}}}type").text = kind
    points = sum(len(line) for line in lines)
    times = time_s if time_s and len(time_s) == points else None
    altitudes = altitude_m if altitude_m and len(altitude_m) == points else None
    i = 0
    for line in lines:
        segment = ET.SubElement(trk, f"{{{NS}}}trkseg")
        for lng, lat, *_ in line:
            point = ET.SubElement(segment, f"{{{NS}}}trkpt", lat=f"{lat:.7f}", lon=f"{lng:.7f}")
            if altitudes and altitudes[i] is not None:
                ET.SubElement(point, f"{{{NS}}}ele").text = f"{altitudes[i]:.1f}"
            if times:
                ET.SubElement(point, f"{{{NS}}}time").text = _iso(
                    start_at + timedelta(seconds=times[i])
                )
            i += 1
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
