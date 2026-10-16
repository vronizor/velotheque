import bisect
import itertools
import math

MAX_GAP_M = 500.0
CLIMB_THRESHOLD_M = 10.0


def haversine_m(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6_371_008.8 * math.asin(math.sqrt(h))


def split_segments(latlng: list, max_gap_m: float = MAX_GAP_M) -> list[list]:
    segments, current = [], []
    for point in latlng:
        if not point or len(point) < 2:
            continue
        if current and haversine_m(current[-1], point) > max_gap_m:
            segments.append(current)
            current = []
        current.append(point)
    segments.append(current)
    return [s for s in segments if len(s) >= 2]


def multilinestring_wkt(segments: list[list]) -> str:
    parts = ", ".join(
        "(" + ", ".join(f"{lng} {lat}" for lat, lng in segment) + ")" for segment in segments
    )
    return f"MULTILINESTRING({parts})"


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Google encoded polyline → [(lat, lng), …]. Raises ValueError on malformed input."""
    factor = 10**precision
    coords, index, lat, lng = [], 0, 0, 0
    while index < len(encoded):
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                if index >= len(encoded):
                    raise ValueError("truncated polyline")
                byte = ord(encoded[index]) - 63
                index += 1
                if byte < 0 or byte > 63:
                    raise ValueError("invalid polyline character")
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lng += deltas[1]
        coords.append((lat / factor, lng / factor))
    return coords


def encode_polyline(coords: list[tuple[float, float]], precision: int = 5) -> str:
    factor = 10**precision
    out, prev_lat, prev_lng = [], 0, 0
    for lat, lng in coords:
        ilat, ilng = round(lat * factor), round(lng * factor)
        for delta in (ilat - prev_lat, ilng - prev_lng):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev_lat, prev_lng = ilat, ilng
    return "".join(out)


def polygon_wkt(ring: list[tuple[float, float]]) -> str:
    points = list(ring) + [ring[0]]
    return "POLYGON((" + ", ".join(f"{lng} {lat}" for lat, lng in points) + "))"


def elevation_gain(altitudes, threshold_m: float = CLIMB_THRESHOLD_M) -> float:
    """Sum of climbs, each counted once it rises threshold_m above the last low and ended by
    a drop of threshold_m, so GPS and DEM noise doesn't add up (10 m matches Strava's totals
    on real rides: median ratio 0.997)."""
    total, low, high, climbing = 0.0, None, None, False
    for value in altitudes:
        if value is None:
            continue
        if low is None:
            low = high = value
        elif climbing:
            if value > high:
                high = value
            elif high - value >= threshold_m:
                total += high - low
                low, high, climbing = value, value, False
        elif value < low:
            low = high = value
        elif value - low >= threshold_m:
            climbing, high = True, value
    return total + (high - low if climbing else 0.0)


PROFILE_POINTS = 1000
GRADE_WINDOW_M = 100.0


def _interpolate(xs: list[float], ys: list[float], x: float) -> float:
    i = bisect.bisect_left(xs, x)
    if i == 0:
        return ys[0]
    if i >= len(xs):
        return ys[-1]
    x0, x1 = xs[i - 1], xs[i]
    t = (x - x0) / (x1 - x0) if x1 > x0 else 0
    return ys[i - 1] + t * (ys[i] - ys[i - 1])


def elevation_profile(
    distances, altitudes, coords, points: int = PROFILE_POINTS, window_m: float = GRADE_WINDOW_M
) -> list[tuple] | None:
    """[(km, altitude m, grade %, lng, lat)] for a chart: about `points` samples evenly spread
    by distance, grade over ±window_m so GPS noise doesn't show as 30% ramps. `coords` are
    the track's [lng, lat]; when they don't line up with the streams (points without GPS were
    dropped from the line), positions are interpolated along the line by distance share."""
    rows = [
        (d, a, i)
        for i, (d, a) in enumerate(zip(distances or [], altitudes or [], strict=False))
        if d is not None and a is not None
    ]
    if len(rows) < 2 or rows[-1][0] <= rows[0][0]:
        return None
    ds, alts = [r[0] for r in rows], [r[1] for r in rows]
    aligned = len(coords) == len(distances)
    if not aligned:
        along = [0.0]
        for a, b in itertools.pairwise(coords):
            along.append(along[-1] + haversine_m(a[::-1], b[::-1]))
        lngs, lats = [c[0] for c in coords], [c[1] for c in coords]
    start, end = ds[0], ds[-1]
    step = (end - start) / points
    out, mark = [], start
    for d, a, i in rows:
        if d < mark and d != end:
            continue
        mark = d + step
        lo, hi = max(d - window_m, start), min(d + window_m, end)
        grade = (_interpolate(ds, alts, hi) - _interpolate(ds, alts, lo)) / (hi - lo) * 100
        if aligned:
            lng, lat = coords[i]
        else:
            x = (d - start) / (end - start) * along[-1]
            lng, lat = _interpolate(along, lngs, x), _interpolate(along, lats, x)
        out.append((round(d / 1000, 3), round(a, 1), round(grade, 1), round(lng, 6), round(lat, 6)))
    return out
