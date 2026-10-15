import re
from datetime import date, datetime, time, timedelta

from sqlalchemy import exists, func, or_

from velotheque.filters.registry import Entity, Field, register
from velotheque.geo import decode_polyline, polygon_wkt
from velotheque.tables import activity_track

RIDES = frozenset({Entity.RIDES})
RIDES_AND_ROUTES = frozenset({Entity.RIDES, Entity.ROUTES})
NO_BIKE = "none"


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _non_negative(value: str) -> float:
    number = float(value)
    if number < 0 or number != number:
        raise ValueError(value)
    return number


def _bike(value: str) -> int | str:
    return NO_BIKE if value == NO_BIKE else int(value)


def _bike_where(t, values):
    ids = [v for v in values if v != NO_BIKE]
    clauses = [t.c.bike_id.in_(ids)] if ids else []
    if NO_BIKE in values:
        clauses.append(t.c.bike_id.is_(None))
    return or_(*clauses)


def _range(param: str, label: str, column: str, scale: float, entities, op: str) -> Field:
    def where(t, values):
        bound = values[0] * scale
        return t.c[column] >= bound if op == "min" else t.c[column] <= bound

    return register(Field(param, label, entities, _non_negative, where))


register(
    Field(
        "after",
        "From",
        RIDES,
        _date,
        lambda t, v: t.c.start_at_local >= datetime.combine(v[0], time.min),
    )
)
register(
    Field(
        "before",
        "To",
        RIDES,
        _date,
        lambda t, v: t.c.start_at_local < datetime.combine(v[0] + timedelta(days=1), time.min),
    )
)
register(
    Field(
        "type",
        "Type",
        RIDES,
        str,
        lambda t, v: t.c.type.in_(v),
        multi=True,
        facet=lambda t: t.c.type,
    )
)
register(Field("bike", "Bike", RIDES, _bike, _bike_where, multi=True, facet=lambda t: t.c.bike_id))

_range("dist_min", "Min distance (km)", "distance_m", 1000, RIDES_AND_ROUTES, "min")
_range("dist_max", "Max distance (km)", "distance_m", 1000, RIDES_AND_ROUTES, "max")
_range("elev_min", "Min elevation (m)", "elevation_gain_m", 1, RIDES_AND_ROUTES, "min")
_range("elev_max", "Max elevation (m)", "elevation_gain_m", 1, RIDES_AND_ROUTES, "max")
_range("dur_min", "Min moving time (h)", "moving_time_s", 3600, RIDES, "min")
_range("dur_max", "Max moving time (h)", "moving_time_s", 3600, RIDES, "max")


FUZZY_MIN_LENGTH = 4
FUZZY_THRESHOLD = 0.5
_WORD = re.compile(r"\w+")


def _query(value: str) -> str:
    if len(value) > 200:
        raise ValueError("search text too long")
    return value


def _q_where(t, values):
    """Accent-insensitive prefix match on every word of name + description (no stemming:
    titles mix French, Spanish, Catalan and English), or a typo-tolerant match on the name."""
    text_value = values[0]
    words = _WORD.findall(text_value)
    clauses = []
    if words:
        tsquery = " & ".join(f"{word}:*" for word in words)
        clauses.append(
            t.c.search_tsv.op("@@")(func.to_tsquery("simple", func.vt_unaccent(tsquery)))
        )
    if len(text_value) >= FUZZY_MIN_LENGTH:
        similarity = func.word_similarity(func.vt_unaccent(text_value), func.vt_unaccent(t.c.name))
        clauses.append(similarity >= FUZZY_THRESHOLD)
    return or_(*clauses) if clauses else t.c.id.is_(None)


def _flag(value: str) -> bool:
    if value.lower() in ("1", "true", "yes", "on"):
        return True
    if value.lower() in ("0", "false", "no", "off"):
        return False
    raise ValueError(value)


def _bbox(value: str) -> tuple[float, float, float, float]:
    west, south, east, north = (float(v) for v in value.split(","))
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError(value)
    return west, south, east, north


def _area(value: str) -> str:
    ring = decode_polyline(value)
    if len(ring) < 3:
        raise ValueError("an area needs at least three points")
    if any(not (-90 <= lat <= 90 and -180 <= lng <= 180) for lat, lng in ring):
        raise ValueError("area out of range")
    return polygon_wkt(ring)


def _intersects(t, geometry):
    if "geometry" in t.c:
        return func.ST_Intersects(t.c.geometry, geometry)
    return exists().where(
        activity_track.c.activity_id == t.c.id,
        func.ST_Intersects(activity_track.c.geometry, geometry),
    )


register(Field("q", "Search", RIDES_AND_ROUTES, _query, _q_where))
register(
    Field(
        "photos",
        "Photos",
        RIDES,
        _flag,
        lambda t, v: t.c.has_photos.is_(v[0]),
        facet=lambda t: t.c.has_photos,
    )
)
register(
    Field(
        "bbox",
        "Map rectangle",
        RIDES_AND_ROUTES,
        _bbox,
        lambda t, v: _intersects(t, func.ST_MakeEnvelope(*v[0], 4326)),
    )
)
register(
    Field(
        "area",
        "Map area",
        RIDES_AND_ROUTES,
        _area,
        lambda t, v: _intersects(t, func.ST_MakeValid(func.ST_GeomFromText(v[0], 4326))),
    )
)
