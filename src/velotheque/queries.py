import json
from dataclasses import dataclass

from sqlalchemy import JSON, Connection, Text, and_, cast, func, literal_column, select, text

from velotheque.exportrow import gear_id, sport_type
from velotheque.filters import Entity, FilterState, build_where, facet_counts
from velotheque.geo import elevation_profile, haversine_m
from velotheque.tables import (
    ROUTE_SOURCES,
    activity,
    activity_photo,
    activity_track,
    activity_track_simplified,
    bike,
    raw_gpx_file,
    route,
    route_coverage,
    route_file,
    route_ride_overlap,
    route_simplified,
    saved_view,
)

SORTS = {
    "date": activity.c.start_at_local,
    "name": func.lower(activity.c.name),
    "distance": activity.c.distance_m,
    "elevation": activity.c.elevation_gain_m,
    "duration": activity.c.moving_time_s,
}
DEFAULT_SORT = "-date"
LIST_LIMIT = 500


ROUTE_SORTS = {
    "name": func.lower(route.c.name),
    "distance": route.c.distance_m,
    "elevation": route.c.elevation_gain_m,
    "coverage": route_coverage.c.coverage_fraction,
    "added": route.c.created_at,
}
DEFAULT_ROUTE_SORT = "name"
DESCENDING_FIRST = {"date", "distance", "elevation", "duration", "coverage", "added"}


@dataclass(frozen=True)
class Sort:
    key: str
    descending: bool

    @classmethod
    def parse(cls, value: str | None, sorts=SORTS, default: str = DEFAULT_SORT) -> "Sort":
        value = value or default
        key = value.lstrip("-")
        if key not in sorts:
            return cls.parse(default, sorts, default)
        return cls(key, value.startswith("-"))

    def toggled(self, key: str) -> str:
        if key == self.key:
            return key if self.descending else f"-{key}"
        return f"-{key}" if key in DESCENDING_FIRST else key

    def __str__(self) -> str:
        return f"-{self.key}" if self.descending else self.key


def _where(state: FilterState):
    return build_where(state, Entity.RIDES, activity)


def ride_rows(conn: Connection, state: FilterState, sort: Sort, limit: int = LIST_LIMIT):
    column = SORTS[sort.key]
    order = column.desc().nulls_last() if sort.descending else column.asc()
    query = (
        select(
            activity.c.id,
            activity.c.name,
            activity.c.type,
            activity.c.start_at_local,
            activity.c.distance_m,
            activity.c.elevation_gain_m,
            activity.c.moving_time_s,
            bike.c.name.label("bike_name"),
        )
        .select_from(activity.outerjoin(bike, bike.c.id == activity.c.bike_id))
        .where(*_where(state))
        .order_by(order, activity.c.id.desc())
        .limit(limit)
    )
    return conn.execute(query).all()


def _extent(geometry):
    extent = func.ST_Extent(geometry)
    return select(
        func.ST_XMin(extent), func.ST_YMin(extent), func.ST_XMax(extent), func.ST_YMax(extent)
    )


def _bbox(row) -> list | None:
    return list(row) if row[0] is not None else None


def ride_totals(conn: Connection, state: FilterState):
    bbox_query = (
        _extent(activity_track_simplified.c.geometry)
        .select_from(
            activity.join(
                activity_track_simplified,
                and_(
                    activity_track_simplified.c.activity_id == activity.c.id,
                    activity_track_simplified.c.level == "overview",
                ),
            )
        )
        .where(*_where(state))
    )
    totals = conn.execute(
        select(
            func.count().label("count"),
            func.coalesce(func.sum(activity.c.distance_m), 0).label("distance_m"),
            func.coalesce(func.sum(activity.c.elevation_gain_m), 0).label("elevation_gain_m"),
            func.coalesce(func.sum(activity.c.moving_time_s), 0).label("moving_time_s"),
        ).where(*_where(state))
    ).one()
    return totals, _bbox(conn.execute(bbox_query).one())


def _feature_collection(conn: Connection, query_from, feature_id, name, geometry, where) -> str:
    feature = func.json_build_object(
        "type",
        "Feature",
        "id",
        feature_id,
        "properties",
        func.json_build_object("name", name),
        "geometry",
        cast(func.ST_AsGeoJSON(geometry, 5), JSON),
    )
    features = conn.execute(
        select(cast(func.coalesce(func.json_agg(feature), literal_column("'[]'::json")), Text))
        .select_from(query_from)
        .where(*where)
    ).scalar_one()
    return '{"type":"FeatureCollection","features":' + features + "}"


def rides_geojson(conn: Connection, state: FilterState, level: str = "overview") -> str:
    s = activity_track_simplified
    return _feature_collection(
        conn,
        activity.join(s, and_(s.c.activity_id == activity.c.id, s.c.level == level)),
        activity.c.id,
        activity.c.name,
        s.c.geometry,
        _where(state),
    )


def ride_types(conn: Connection) -> list[str]:
    return (
        conn.execute(select(activity.c.type).distinct().order_by(activity.c.type)).scalars().all()
    )


def bikes(conn: Connection):
    return conn.execute(select(bike).order_by(bike.c.retired, bike.c.name)).all()


def ride(conn: Connection, activity_id: int):
    return conn.execute(
        select(activity, bike.c.name.label("bike_name"))
        .select_from(activity.outerjoin(bike, bike.c.id == activity.c.bike_id))
        .where(activity.c.id == activity_id)
    ).first()


def ride_track_geojson(conn: Connection, activity_id: int) -> str | None:
    s = activity_track_simplified
    return conn.execute(
        select(func.ST_AsGeoJSON(s.c.geometry, 6)).where(
            s.c.activity_id == activity_id, s.c.level == "detail"
        )
    ).scalar()


def ride_profile(conn: Connection, activity_id: int) -> str | None:
    t = activity_track
    found = conn.execute(
        select(t.c.distance_m, t.c.altitude_m, func.ST_AsGeoJSON(t.c.geometry, 6)).where(
            t.c.activity_id == activity_id
        )
    ).first()
    if found is None or not found.altitude_m:
        return None
    coords = [c for line in json.loads(found[2])["coordinates"] for c in line]
    profile = elevation_profile(found.distance_m, found.altitude_m, coords)
    return json.dumps(profile, separators=(",", ":")) if profile else None


FACETS = ("type", "bike", "photos")


def ride_facets(conn: Connection, state: FilterState) -> dict[str, dict]:
    return {p: facet_counts(conn, state, Entity.RIDES, activity, p) for p in FACETS}


def ride_photos(conn: Connection, activity_id: int):
    return conn.execute(
        select(activity_photo)
        .where(activity_photo.c.activity_id == activity_id)
        .order_by(activity_photo.c.position)
    ).all()


def saved_views(conn: Connection, entity: str = "rides"):
    return conn.execute(
        select(saved_view).where(saved_view.c.entity == entity).order_by(saved_view.c.name)
    ).all()


LATEST_EXPORT_ROWS = text(
    """
    SELECT DISTINCT ON (source_id) source_id, export_name, payload, fetched_at
    FROM raw_export_activity
    ORDER BY source_id, id DESC
    """
)


def export_status(conn: Connection, sport_types) -> dict:
    rows = conn.execute(LATEST_EXPORT_ROWS).all()
    if not rows:
        return {"rows": 0}
    unmatched = []
    for row in rows:
        payload = row.payload
        if sport_type(payload.get("Activity Type")) not in sport_types:
            continue
        if payload.get("Activity Gear") and not gear_id(payload):
            unmatched.append(row)
    ids = [r.source_id for r in unmatched]
    by_source = dict(
        conn.execute(
            select(activity.c.source_id, activity.c.id).where(activity.c.source_id.in_(ids))
        ).all()
    )
    return {
        "rows": len(rows),
        "exports": sorted({r.export_name for r in rows}),
        "last_import": max(r.fetched_at for r in rows),
        "photos": conn.execute(select(func.count()).select_from(activity_photo)).scalar(),
        "unmatched": [
            {
                "source_id": r.source_id,
                "activity_id": by_source.get(r.source_id),
                "name": r.payload.get("Activity Name"),
                "gear": r.payload.get("Activity Gear"),
                "date": r.payload.get("Activity Date"),
            }
            for r in unmatched
        ],
    }


def _route_where(state: FilterState):
    return build_where(state, Entity.ROUTES, route)


def _with_coverage():
    return route.outerjoin(route_coverage, route_coverage.c.route_id == route.c.id)


def route_rows(conn: Connection, state: FilterState, sort: Sort, limit: int = LIST_LIMIT):
    column = ROUTE_SORTS[sort.key]
    order = column.desc().nulls_last() if sort.descending else column.asc().nulls_last()
    return conn.execute(
        select(
            route.c.id,
            route.c.name,
            route.c.source,
            route.c.tags,
            route.c.distance_m,
            route.c.elevation_gain_m,
            route_coverage.c.coverage_fraction,
        )
        .select_from(_with_coverage())
        .where(*_route_where(state))
        .order_by(order, route.c.id)
        .limit(limit)
    ).all()


def route_totals(conn: Connection, state: FilterState):
    totals = conn.execute(
        select(
            func.count().label("count"),
            func.coalesce(func.sum(route.c.distance_m), 0).label("distance_m"),
            func.coalesce(func.sum(route.c.elevation_gain_m), 0).label("elevation_gain_m"),
        ).where(*_route_where(state))
    ).one()
    bbox = conn.execute(
        _extent(route_simplified.c.geometry)
        .select_from(
            route.join(
                route_simplified,
                and_(
                    route_simplified.c.route_id == route.c.id,
                    route_simplified.c.level == "overview",
                ),
            )
        )
        .where(*_route_where(state))
    ).one()
    return totals, _bbox(bbox)


def routes_geojson(conn: Connection, state: FilterState, level: str = "overview") -> str:
    s = route_simplified
    return _feature_collection(
        conn,
        route.join(s, and_(s.c.route_id == route.c.id, s.c.level == level)),
        route.c.id,
        route.c.name,
        s.c.geometry,
        _route_where(state),
    )


ROUTE_FACETS = ("tag", "source")


def route_facets(conn: Connection, state: FilterState) -> dict[str, dict]:
    return {p: facet_counts(conn, state, Entity.ROUTES, route, p) for p in ROUTE_FACETS}


def all_tags(conn: Connection) -> list[str]:
    tags = select(func.unnest(route.c.tags).label("tag")).distinct().subquery()
    return conn.execute(select(tags.c.tag).order_by(func.lower(tags.c.tag))).scalars().all()


def all_sources(conn: Connection, suggestions: bool = True) -> list[str]:
    """Sources in use, then (as suggestions) the built-in ones not used yet."""
    sources = select(route.c.source).distinct().subquery()
    used = (
        conn.execute(select(sources.c.source).order_by(func.lower(sources.c.source)))
        .scalars()
        .all()
    )
    if not suggestions:
        return list(used)
    lowered = {s.lower() for s in used}
    return [*used, *(s for s in ROUTE_SOURCES if s.lower() not in lowered)]


ROUTE_BULKY = {"geometry", "altitude_m", "search_tsv"}


def route_detail(conn: Connection, route_id: int):
    return conn.execute(
        select(
            *(c for c in route.c if c.name not in ROUTE_BULKY),
            route_coverage.c.coverage_fraction,
            route_coverage.c.covered_m,
            route_coverage.c.length_m.label("ridable_m"),
        )
        .select_from(_with_coverage())
        .where(route.c.id == route_id)
    ).first()


def route_files(conn: Connection, route_id: int):
    return conn.execute(
        select(
            raw_gpx_file.c.id,
            raw_gpx_file.c.filename,
            func.length(raw_gpx_file.c.content).label("size"),
        )
        .join(route_file, route_file.c.raw_gpx_file_id == raw_gpx_file.c.id)
        .where(route_file.c.route_id == route_id)
        .order_by(route_file.c.position)
    ).all()


def route_file_content(conn: Connection, route_id: int, file_id: int):
    return conn.execute(
        select(raw_gpx_file.c.filename, raw_gpx_file.c.content)
        .join(route_file, route_file.c.raw_gpx_file_id == raw_gpx_file.c.id)
        .where(route_file.c.route_id == route_id, raw_gpx_file.c.id == file_id)
    ).first()


def route_profile(conn: Connection, route_id: int) -> str | None:
    """Like ride_profile; distances are measured along the route's pieces without bridging
    the gaps between them, as distance_m is."""
    found = conn.execute(
        select(route.c.altitude_m, func.ST_AsGeoJSON(route.c.geometry, 6)).where(
            route.c.id == route_id
        )
    ).first()
    if found is None or not found.altitude_m:
        return None
    coords, distances = [], []
    for line in json.loads(found[1])["coordinates"]:
        for i, point in enumerate(line):
            step = haversine_m(line[i - 1][::-1], point[::-1]) if i else 0.0
            distances.append((distances[-1] if distances else 0.0) + step)
            coords.append(point)
    profile = elevation_profile(distances, found.altitude_m, coords)
    return json.dumps(profile, separators=(",", ":")) if profile else None


def route_map(conn: Connection, route_id: int) -> dict:
    """Route (detail), its ridden parts and the overlapping rides, as GeoJSON strings."""
    s = route_simplified
    line = conn.execute(
        select(func.ST_AsGeoJSON(s.c.geometry, 6)).where(
            s.c.route_id == route_id, s.c.level == "detail"
        )
    ).scalar()
    covered = conn.execute(
        select(func.ST_AsGeoJSON(route_coverage.c.covered, 6)).where(
            route_coverage.c.route_id == route_id
        )
    ).scalar()
    a = activity_track_simplified
    rides = _feature_collection(
        conn,
        activity.join(a, and_(a.c.activity_id == activity.c.id, a.c.level == "overview")).join(
            route_ride_overlap, route_ride_overlap.c.activity_id == activity.c.id
        ),
        activity.c.id,
        activity.c.name,
        a.c.geometry,
        [route_ride_overlap.c.route_id == route_id],
    )
    return {"line": line, "covered": covered, "rides": rides}


def ride_on_route(conn: Connection, route_id: int, activity_id: int) -> str | None:
    """The part of a route one ride covered, as GeoJSON (~1 m simplification)."""
    o = route_ride_overlap
    return conn.execute(
        select(func.ST_AsGeoJSON(func.ST_Simplify(o.c.covered, 1e-5), 6)).where(
            o.c.route_id == route_id, o.c.activity_id == activity_id
        )
    ).scalar()


def route_rides(conn: Connection, route_id: int):
    o = route_ride_overlap
    return conn.execute(
        select(
            activity.c.id,
            activity.c.name,
            activity.c.start_at_local,
            activity.c.distance_m,
            o.c.overlap_m,
            o.c.overlap_fraction,
        )
        .join(o, o.c.activity_id == activity.c.id)
        .where(o.c.route_id == route_id)
        .order_by(o.c.overlap_fraction.desc(), activity.c.start_at_local.desc())
    ).all()


def ride_routes(conn: Connection, activity_id: int):
    o = route_ride_overlap
    return conn.execute(
        select(route.c.id, route.c.name, route.c.tags, o.c.overlap_m, o.c.overlap_fraction)
        .join(o, o.c.route_id == route.c.id)
        .where(o.c.activity_id == activity_id)
        .order_by(o.c.overlap_m.desc())
    ).all()


def ride_routes_geojson(conn: Connection, activity_id: int) -> str:
    """The routes a ride follows (overview lines; races run to thousands of km)."""
    s = route_simplified
    return _feature_collection(
        conn,
        route.join(s, and_(s.c.route_id == route.c.id, s.c.level == "overview")).join(
            route_ride_overlap, route_ride_overlap.c.route_id == route.c.id
        ),
        route.c.id,
        route.c.name,
        s.c.geometry,
        [route_ride_overlap.c.activity_id == activity_id],
    )


def route_name(conn: Connection, route_id: int) -> str | None:
    return conn.execute(select(route.c.name).where(route.c.id == route_id)).scalar()
