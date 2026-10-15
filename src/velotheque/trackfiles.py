import gzip
import io
import warnings
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fitdecode

from velotheque.geo import haversine_m

SEMICIRCLE = 180 / 2**31


class _TolerantProcessor(fitdecode.DefaultDataProcessor):
    """Some Strava-exported FIT files carry array-valued date_time fields that make
    fitdecode's timestamp conversion raise; leave those values unconverted."""

    def process_type_date_time(self, reader, field_data):
        if not isinstance(field_data.value, tuple):
            super().process_type_date_time(reader, field_data)

    def process_type_local_date_time(self, reader, field_data):
        if not isinstance(field_data.value, tuple):
            super().process_type_local_date_time(reader, field_data)


@dataclass
class Track:
    latlng: list[list[float]] = field(default_factory=list)
    times: list[datetime | None] = field(default_factory=list)
    distance_m: list[float | None] = field(default_factory=list)
    altitude_m: list[float | None] = field(default_factory=list)

    def add(self, lat, lng, when=None, distance=None, altitude=None) -> None:
        self.latlng.append([lat, lng])
        self.times.append(when)
        self.distance_m.append(distance)
        self.altitude_m.append(altitude)

    def time_s(self) -> list[int] | None:
        if not self.times or any(t is None for t in self.times):
            return None
        start = self.times[0]
        return [round((t - start).total_seconds()) for t in self.times]

    def distances(self) -> list[float]:
        if self.distance_m and all(d is not None for d in self.distance_m):
            return self.distance_m
        total, out = 0.0, []
        for i, point in enumerate(self.latlng):
            if i:
                total += haversine_m(self.latlng[i - 1], point)
            out.append(round(total, 1))
        return out

    def altitudes(self) -> list[float] | None:
        if any(a is not None for a in self.altitude_m):
            return self.altitude_m
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element, name: str):
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _text(element, *path: str) -> str | None:
    for name in path:
        element = _child(element, name) if element is not None else None
    return element.text.strip() if element is not None and element.text else None


def _float(value: str | None) -> float | None:
    return float(value) if value not in (None, "") else None


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def parse_gpx(data: bytes) -> Track:
    track = Track()
    for element in ET.fromstring(data.lstrip()).iter():
        if _local(element.tag) != "trkpt":
            continue
        track.add(
            float(element.get("lat")),
            float(element.get("lon")),
            _time(_text(element, "time")),
            None,
            _float(_text(element, "ele")),
        )
    return track


def parse_tcx(data: bytes) -> Track:
    track = Track()
    for element in ET.fromstring(data.lstrip()).iter():
        if _local(element.tag) != "Trackpoint":
            continue
        lat = _text(element, "Position", "LatitudeDegrees")
        lng = _text(element, "Position", "LongitudeDegrees")
        if lat is None or lng is None:
            continue
        track.add(
            float(lat),
            float(lng),
            _time(_text(element, "Time")),
            _float(_text(element, "DistanceMeters")),
            _float(_text(element, "AltitudeMeters")),
        )
    return track


FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)
# Some exported FIT files declare record fields with the raw `byte` base type, so fitdecode
# hands back byte tuples; decode them with the profile's real type, scale and offset.
BYTE_FIELDS = {
    "position_lat": (True, 1, 0, 0x7FFFFFFF),
    "position_long": (True, 1, 0, 0x7FFFFFFF),
    "timestamp": (False, 1, 0, 0xFFFFFFFF),
    "distance": (False, 100, 0, 0xFFFFFFFF),
    "altitude": (False, 5, 500, 0xFFFF),
}


def _fit_value(frame, name: str):
    if not frame.has_field(name):
        return None
    field = frame.get_field(name)
    if not isinstance(field.raw_value, tuple):
        return field.value
    signed, scale, offset, invalid = BYTE_FIELDS[name]
    order = "big" if frame.def_mesg.endian == ">" else "little"
    raw = bytes(field.raw_value)
    if int.from_bytes(raw, order, signed=False) == invalid:
        return None
    number = int.from_bytes(raw, order, signed=signed)
    if name == "timestamp":
        return FIT_EPOCH + timedelta(seconds=number)
    return number / scale - offset


def parse_fit(data: bytes) -> Track:
    track = Track()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        reader = fitdecode.FitReader(
            io.BytesIO(data),
            processor=_TolerantProcessor(),
            check_crc=fitdecode.CrcCheck.DISABLED,
            keep_raw_chunks=False,
        )
        with reader as fit:
            for frame in fit:
                if not isinstance(frame, fitdecode.FitDataMessage) or frame.name != "record":
                    continue
                lat, lng = _fit_value(frame, "position_lat"), _fit_value(frame, "position_long")
                if lat is None or lng is None:
                    continue
                altitude = None
                if not isinstance(frame.get_raw_value("altitude", fallback=None), tuple):
                    altitude = frame.get_value("enhanced_altitude", fallback=None)
                if altitude is None:
                    altitude = _fit_value(frame, "altitude")
                track.add(
                    lat * SEMICIRCLE,
                    lng * SEMICIRCLE,
                    _fit_value(frame, "timestamp"),
                    _fit_value(frame, "distance"),
                    altitude,
                )
    return track


PARSERS = {".gpx": parse_gpx, ".tcx": parse_tcx, ".fit": parse_fit}


def parse_track_file(path: Path) -> Track:
    data = path.read_bytes()
    suffixes = path.suffixes
    if suffixes and suffixes[-1] == ".gz":
        data = gzip.decompress(data)
        suffixes = suffixes[:-1]
    parser = PARSERS.get(suffixes[-1].lower() if suffixes else "")
    if parser is None:
        raise ValueError(f"unsupported track file {path.name}")
    return parser(data)
