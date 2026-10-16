import json
import re
from dataclasses import dataclass

from sqlalchemy import JSON, Connection, Text, and_, cast, func, literal_column, select, text

from velotheque.exportrow import gear_id, sport_type
from velotheque.filters import Entity, FilterState, build_where, facet_counts
from velotheque.filters.fields import NO_REGION
from velotheque.geo import elevation_profile, haversine_m
from velotheque.tables import (
    ROUTE_SOURCES,
    activity,
    activity_companion,
    activity_photo,
    activity_region,
    activity_track,
    activity_track_simplified,
    bike,
    companion,
    raw_gpx_file,
    region,
    region_country,
    route,
    route_coverage,
    route_file,
    route_region,
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


FACETS = ("type", "bike", "group", "with", "photos", "region")


def ride_facets(conn: Connection, state: FilterState) -> dict[str, dict]:
    return {p: facet_counts(conn, state, Entity.RIDES, activity, p) for p in FACETS}


def companions(conn: Connection):
    """Everyone ridden with, most rides first, with the rides they're shown on."""
    c = activity_companion
    rides = func.count(c.c.activity_id).filter(~c.c.hidden)
    return conn.execute(
        select(
            companion.c.id,
            companion.c.name,
            (func.cardinality(companion.c.strava_athlete_ids) > 0).label("on_strava"),
            rides.label("rides"),
        )
        .select_from(companion.outerjoin(c, c.c.companion_id == companion.c.id))
        .group_by(companion.c.id)
        .order_by(rides.desc(), func.lower(companion.c.name))
    ).all()


def ride_companions(conn: Connection, activity_id: int):
    c = activity_companion
    return conn.execute(
        select(companion.c.id, companion.c.name, c.c.source)
        .select_from(c.join(companion, companion.c.id == c.c.companion_id))
        .where(c.c.activity_id == activity_id, ~c.c.hidden)
        .order_by(func.lower(companion.c.name))
    ).all()


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


ROUTE_FACETS = ("tag", "source", "region")


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
        select(
            route.c.id,
            route.c.name,
            route.c.tags,
            route.c.distance_m,
            o.c.overlap_m,
            o.c.overlap_fraction,
        )
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


def region_status(conn: Connection) -> dict:
    def untagged(table, key):
        return conn.execute(
            select(func.count())
            .select_from(table)
            .where(~select(key).where(key == table.c.id).exists())
        ).scalar()

    has_track = select(activity_track.c.activity_id).where(
        activity_track.c.activity_id == activity.c.id
    )
    return {
        "countries": conn.execute(select(region_country).order_by(region_country.c.name)).all(),
        "rides_untagged": untagged(activity, activity_region.c.activity_id),
        "rides_no_gps": conn.execute(
            select(func.count()).select_from(activity).where(~has_track.exists())
        ).scalar(),
        "routes_untagged": untagged(route, route_region.c.route_id),
    }


def _context(row) -> str:
    """Where a region sits: nothing for a country, else its parent and country."""
    if row.level == 0:
        return ""
    if row.level == 1 or row.parent_name == row.country_name:
        return row.country_name
    return f"{row.parent_name}, {row.country_name}"


def region_labels(conn: Connection, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    parent, country = region.alias("parent"), region.alias("country_region")
    rows = conn.execute(
        select(
            region.c.id,
            region.c.name,
            region.c.level,
            region.c.type_label,
            parent.c.name.label("parent_name"),
            country.c.name.label("country_name"),
        )
        .select_from(
            region.outerjoin(parent, parent.c.id == region.c.parent_id).outerjoin(
                country, country.c.id == region.c.country
            )
        )
        .where(region.c.id.in_(ids))
    ).all()
    return {
        r.id: {
            "id": r.id,
            "name": r.name,
            "level": r.level,
            "type": r.type_label,
            "context": _context(r),
        }
        for r in rows
    }


def region_filter(conn: Connection, state: FilterState) -> dict:
    """The Region fieldset: selected regions first, then every loaded country."""
    chosen = [v for v in state.values.get("region", []) if v != NO_REGION]
    countries = conn.execute(
        select(region.c.id).where(region.c.level == 0).order_by(region.c.name)
    ).scalars()
    ids = chosen + [c for c in countries if c not in chosen]
    labels = region_labels(conn, ids)
    return {
        "shown": [labels[i] for i in ids if i in labels],
        "loaded": bool(ids),
        "none": NO_REGION in state.values.get("region", []),
    }


def region_counts(conn: Connection, state: FilterState, entity: str) -> dict[str, dict]:
    counts = {}
    if entity in ("rides", "both"):
        counts["rides"] = facet_counts(conn, state, Entity.RIDES, activity, "region")
    if entity in ("routes", "both"):
        counts["routes"] = facet_counts(conn, state, Entity.ROUTES, route, "region")
    return counts


SUGGESTIONS = 12


def search_regions(
    conn: Connection, state: FilterState, term: str, entity: str, limit: int = SUGGESTIONS
) -> tuple[list[dict], dict[str, dict]]:
    """Regions whose name or a variant name has a word starting with the term (accents and
    case ignored), exact names first, then the ones with the most rides/routes."""
    term = term.strip().lower()[:80]
    if not term:
        return [], {}
    escaped = re.sub(r"([^\w\s])", r"\\\1", term)
    rows = conn.execute(
        text(
            """
            SELECT id, search_key ~ ('\\|' || vt_unaccent(:escaped) || '\\|') AS exact
            FROM region
            WHERE search_key ~ ('[^[:alnum:]]' || vt_unaccent(:escaped))
            ORDER BY exact DESC, level, name
            LIMIT 200
            """
        ),
        {"escaped": escaped},
    ).all()
    counts = region_counts(conn, state, entity)

    def total(region_id):
        return sum(c.get(region_id, 0) for c in counts.values())

    ranked = sorted(rows, key=lambda r: (not r.exact, -total(r.id)))[:limit]
    labels = region_labels(conn, [r.id for r in ranked])
    return [labels[r.id] for r in ranked if r.id in labels], counts


def region_outlines(conn: Connection, ids: list[str]) -> str:
    """Selected regions for the map, simplified to ~1/2000 of their size."""
    size = func.greatest(
        func.ST_XMax(region.c.geometry) - func.ST_XMin(region.c.geometry),
        func.ST_YMax(region.c.geometry) - func.ST_YMin(region.c.geometry),
    )
    return _feature_collection(
        conn,
        region,
        region.c.id,
        region.c.name,
        func.ST_SimplifyPreserveTopology(region.c.geometry, size / 2000),
        [region.c.id.in_(ids)],
    )


def _grouped_regions(rows) -> list[dict]:
    """[{country, levels: [(label, [regions])]}] for the ride and route pages."""
    countries: dict[str, dict] = {}
    for r in rows:
        group = countries.setdefault(r.country, {"country": None, "levels": {}})
        if r.level == 0:
            group["country"] = r
        else:
            group["levels"].setdefault(r.level, []).append(r)
    grouped = []
    for group in countries.values():
        levels = []
        for _, regions in sorted(group["levels"].items()):
            labels = list(dict.fromkeys(r.type_label for r in regions))
            levels.append((" / ".join(labels), regions))
        grouped.append({"country": group["country"], "levels": levels})
    return grouped


def _entity_regions(conn: Connection, link, key, entity_id: int) -> list[dict]:
    rows = conn.execute(
        select(region.c.id, region.c.name, region.c.level, region.c.type_label, region.c.country)
        .join(link, link.c.region_id == region.c.id)
        .where(key == entity_id)
        .order_by(region.c.country, region.c.level, func.lower(region.c.name))
    ).all()
    return _grouped_regions(rows)


def ride_regions(conn: Connection, activity_id: int) -> list[dict]:
    return _entity_regions(conn, activity_region, activity_region.c.activity_id, activity_id)


def route_regions(conn: Connection, route_id: int) -> list[dict]:
    return _entity_regions(conn, route_region, route_region.c.route_id, route_id)
