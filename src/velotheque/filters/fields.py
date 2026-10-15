from datetime import date, datetime, time, timedelta

from sqlalchemy import or_

from velotheque.filters.registry import Entity, Field, register

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
register(Field("type", "Type", RIDES, str, lambda t, v: t.c.type.in_(v), multi=True))
register(Field("bike", "Bike", RIDES, _bike, _bike_where, multi=True))

_range("dist_min", "Min distance (km)", "distance_m", 1000, RIDES_AND_ROUTES, "min")
_range("dist_max", "Max distance (km)", "distance_m", 1000, RIDES_AND_ROUTES, "max")
_range("elev_min", "Min elevation (m)", "elevation_gain_m", 1, RIDES_AND_ROUTES, "min")
_range("elev_max", "Max elevation (m)", "elevation_gain_m", 1, RIDES_AND_ROUTES, "max")
_range("dur_min", "Min moving time (h)", "moving_time_s", 3600, RIDES, "min")
_range("dur_max", "Max moving time (h)", "moving_time_s", 3600, RIDES, "max")
