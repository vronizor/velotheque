from dataclasses import dataclass

from sqlalchemy import JSON, Connection, Text, and_, cast, func, literal_column, select

from velotheque.filters import Entity, FilterState, build_where
from velotheque.tables import activity, activity_track_simplified, bike

SORTS = {
    "date": activity.c.start_at_local,
    "name": func.lower(activity.c.name),
    "distance": activity.c.distance_m,
    "elevation": activity.c.elevation_gain_m,
    "duration": activity.c.moving_time_s,
}
DEFAULT_SORT = "-date"
LIST_LIMIT = 500


@dataclass(frozen=True)
class Sort:
    key: str
    descending: bool

    @classmethod
    def parse(cls, value: str | None) -> "Sort":
        value = value or DEFAULT_SORT
        key = value.lstrip("-")
        if key not in SORTS:
            return cls.parse(DEFAULT_SORT)
        return cls(key, value.startswith("-"))

    def toggled(self, key: str) -> str:
        if key == self.key:
            return key if self.descending else f"-{key}"
        return f"-{key}" if key in ("date", "distance", "elevation", "duration") else key

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


def ride_totals(conn: Connection, state: FilterState):
    extent = func.ST_Extent(activity_track_simplified.c.geometry)
    bbox_query = (
        select(
            func.ST_XMin(extent),
            func.ST_YMin(extent),
            func.ST_XMax(extent),
            func.ST_YMax(extent),
        )
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
    bbox = conn.execute(bbox_query).one()
    return totals, (list(bbox) if bbox[0] is not None else None)


def rides_geojson(conn: Connection, state: FilterState, level: str = "overview") -> str:
    s = activity_track_simplified
    feature = func.json_build_object(
        "type",
        "Feature",
        "id",
        activity.c.id,
        "properties",
        func.json_build_object("name", activity.c.name),
        "geometry",
        cast(func.ST_AsGeoJSON(s.c.geometry, 5), JSON),
    )
    features = conn.execute(
        select(cast(func.coalesce(func.json_agg(feature), literal_column("'[]'::json")), Text))
        .select_from(activity.join(s, and_(s.c.activity_id == activity.c.id, s.c.level == level)))
        .where(*_where(state))
    ).scalar_one()
    return '{"type":"FeatureCollection","features":' + features + "}"


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
