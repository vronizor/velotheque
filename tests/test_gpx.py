import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from velotheque import gpx
from velotheque.trackfiles import parse_gpx

START = datetime(2026, 8, 15, 5, 47, tzinfo=UTC)
NS = {"g": "http://www.topografix.com/GPX/1/1"}


def test_ride_gpx_round_trip():
    lines = [[[2.35, 48.85], [2.36, 48.86]], [[2.5, 48.9]]]
    data = gpx.ride_gpx(
        "Col & co <3", "MountainBikeRide", START, lines, [0, 60, 600], [100.0, None, 120.5]
    )
    track = parse_gpx(data)
    assert track.latlng == [[48.85, 2.35], [48.86, 2.36], [48.9, 2.5]]
    assert track.time_s() == [0, 60, 600]
    assert track.altitude_m == [100.0, None, 120.5]
    root = ET.fromstring(data)
    assert len(root.findall("g:trk/g:trkseg", NS)) == 2
    assert root.find("g:trk/g:name", NS).text == "Col & co <3"
    assert root.find("g:trk/g:trkseg/g:trkpt/g:time", NS).text == "2026-08-15T05:47:00Z"


def test_ride_gpx_without_times_or_mismatched_arrays():
    data = gpx.ride_gpx("r", "Ride", START, [[[2.35, 48.85], [2.36, 48.86]]], [0], None)
    root = ET.fromstring(data)
    assert root.find(".//g:trkpt/g:time", NS) is None and root.find(".//g:ele", NS) is None


def test_slugs_and_link():
    assert gpx.slug("Col du Mont 2026 Grand Finale!") == "col-du-mont-2026-grand-finale"
    assert gpx.slug("Pédalada niños 🚲") == "pedalada-ninos"
    assert gpx.slug("🚲", "ride-7") == "ride-7"
    assert gpx.file_slug("Étape 3 – Teruel.GPX") == "etape-3-teruel.gpx"
    assert gpx.file_slug("race") == "race.gpx"
    url = gpx.gpx_studio_url(["https://velo.example.ts.net/rides/1/gpx/a.gpx"])
    assert url.startswith("https://gpx.studio/app?files=%5B%22https%3A%2F%2F")
    files = json.loads(parse_qs(urlsplit(url).query)["files"][0])
    assert files == ["https://velo.example.ts.net/rides/1/gpx/a.gpx"]
