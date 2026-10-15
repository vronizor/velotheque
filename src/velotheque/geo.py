import math

MAX_GAP_M = 500.0


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
