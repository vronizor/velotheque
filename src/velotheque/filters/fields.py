import re
from datetime import date, datetime, time, timedelta

from sqlalchemy import case, exists, func, literal_column, or_, select, true

from velotheque.config import FOOT_SPORT_TYPES
from velotheque.filters.registry import Entity, Field, register
from velotheque.geo import decode_polyline, polygon_wkt
from velotheque.tables import (
    ROUTE_SOURCE_MAX,
    activity_companion,
    activity_region,
    activity_track,
    component_assignment,
    route_coverage,
    route_region,
    route_ride_overlap,
)

RIDES = frozenset({Entity.RIDES})
ROUTES = frozenset({Entity.ROUTES})
RIDES_AND_ROUTES = frozenset({Entity.RIDES, Entity.ROUTES})
NO_BIKE = "none"
NO_REGION = "none"
_GID = re.compile(r"[A-Z0-9]{3}(\.\d+)*(_\d+)?")


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
        clauses.append(t.c.bike_id.is_(None) & t.c.type.not_in(FOOT_SPORT_TYPES))
    return or_(*clauses)


def _bike_facet(t):
    """On-foot activities never have a bike; they're counted apart, not under "No bike"."""
    return case((t.c.type.in_(FOOT_SPORT_TYPES), literal_column("-1")), else_=t.c.bike_id)


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
register(Field("bike", "Bike", RIDES, _bike, _bike_where, multi=True, facet=_bike_facet))

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


register(
    Field(
        "foot",
        "On foot",
        RIDES,
        _flag,
        lambda t, v: true() if v[0] else t.c.type.not_in(FOOT_SPORT_TYPES),
        facet=lambda t: t.c.type.in_(FOOT_SPORT_TYPES),
        default=(False,),
    )
)


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


def _tag(value: str) -> str:
    if len(value) > 40:
        raise ValueError("tag too long")
    return value


def _source(value: str) -> str:
    if len(value) > ROUTE_SOURCE_MAX:
        raise ValueError("source too long")
    return value


DEFAULT_OVERLAP_PCT = 30.0


def _overlap(value: str) -> tuple[int, float]:
    route_id, _, pct = value.partition(":")
    percent = _non_negative(pct) if pct else DEFAULT_OVERLAP_PCT
    if percent > 100:
        raise ValueError(value)
    return int(route_id), percent


def _overlaps_where(t, values):
    o = route_ride_overlap
    return or_(
        *(
            exists().where(
                o.c.activity_id == t.c.id,
                o.c.route_id == route_id,
                o.c.overlap_fraction >= percent / 100,
            )
            for route_id, percent in values
        )
    )


def _coverage(t):
    fraction = (
        select(route_coverage.c.coverage_fraction)
        .where(route_coverage.c.route_id == t.c.id)
        .correlate_except(route_coverage)
    )
    return func.coalesce(fraction.scalar_subquery(), 0)


register(
    Field(
        "tag",
        "Tag",
        ROUTES,
        _tag,
        lambda t, v: t.c.tags.overlap(v),
        multi=True,
        facet=lambda t: func.unnest(t.c.tags),
    )
)
register(
    Field(
        "source",
        "Source",
        ROUTES,
        _source,
        lambda t, v: t.c.source.in_(v),
        multi=True,
        facet=lambda t: t.c.source,
    )
)
register(Field("overlaps_route", "On route", RIDES, _overlap, _overlaps_where, multi=True))
register(
    Field(
        "coverage_min",
        "Min ridden (%)",
        ROUTES,
        _non_negative,
        lambda t, v: _coverage(t) >= v[0] / 100,
    )
)
register(
    Field(
        "coverage_max",
        "Max ridden (%)",
        ROUTES,
        _non_negative,
        lambda t, v: _coverage(t) <= v[0] / 100,
    )
)


def _region(value: str) -> str:
    if value != NO_REGION and not _GID.fullmatch(value):
        raise ValueError(value)
    return value


def _region_links(t):
    if t.name == "route":
        return route_region.c.route_id, route_region.c.region_id
    return activity_region.c.activity_id, activity_region.c.region_id


def _region_where(t, values):
    key, region_id = _region_links(t)
    ids = [v for v in values if v != NO_REGION]
    clauses = [exists().where(key == t.c.id, region_id.in_(ids))] if ids else []
    if NO_REGION in values:
        clauses.append(~exists().where(key == t.c.id))
    return or_(*clauses)


def _region_facet(t):
    """Every region a ride/route is tagged with, or "none"."""
    key, region_id = _region_links(t)
    tagged = select(func.array_agg(region_id)).where(key == t.c.id).scalar_subquery()
    return func.unnest(func.coalesce(tagged, literal_column(f"ARRAY['{NO_REGION}']")))


register(
    Field(
        "region",
        "Region",
        RIDES_AND_ROUTES,
        _region,
        _region_where,
        multi=True,
        facet=_region_facet,
    )
)


GROUPS = ("solo", "group")


def _group(value: str) -> str:
    if value not in GROUPS:
        raise ValueError(value)
    return value


def _group_of(t):
    """Strava's group size: 1 is solo; unknown (export-only rides) is neither."""
    return case((t.c.athlete_count > 1, "group"), (t.c.athlete_count == 1, "solo"))


register(
    Field(
        "group",
        "Group",
        RIDES,
        _group,
        lambda t, v: _group_of(t).in_(v),
        multi=True,
        facet=_group_of,
    )
)


def _shown_companions(t):
    c = activity_companion
    return (c.c.activity_id == t.c.id, ~c.c.hidden)


def _with_where(t, values):
    c = activity_companion
    return exists().where(*_shown_companions(t), c.c.companion_id.in_(values))


def _with_facet(t):
    c = activity_companion
    ids = select(func.array_agg(c.c.companion_id)).where(*_shown_companions(t))
    return func.unnest(ids.scalar_subquery())


register(Field("with", "Rode with", RIDES, int, _with_where, multi=True, facet=_with_facet))


def _component_where(t, values):
    a = component_assignment
    return exists().where(
        a.c.component_id.in_(values),
        a.c.bike_id == t.c.bike_id,
        t.c.start_at >= a.c.mounted_at,
        a.c.removed_at.is_(None) | (t.c.start_at < a.c.removed_at),
    )


register(Field("component", "Component", RIDES, int, _component_where, multi=True))
