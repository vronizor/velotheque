"""Components, where they were mounted, and how worn they are.

Usage of a service type = the rides on the bike the component was on, between mounting
(inclusive) and removal (exclusive), started after the latest reset. A service event of level L
resets the component's service types of level <= L. Nothing here is stored: it is all
computed from curated rows plus the rides."""

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg import errors
from sqlalchemy import Connection, delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from velotheque.config import get_settings
from velotheque.tables import (
    INTERVAL_UNITS,
    RIDE_SCOPES,
    activity,
    bike,
    component,
    component_assignment,
    service_event,
    service_type,
)

NAME_MAX = 120
LOCAL_INPUT = "%Y-%m-%dT%H:%M"
DUE_SOON = 0.9
KINDS = (
    "Chain",
    "Cassette",
    "Chainring",
    "Tyre",
    "Wheel",
    "Brake pads",
    "Brake rotor",
    "Bottom bracket",
    "Headset",
    "Fork",
    "Shock",
    "Derailleur",
    "Cables",
    "Bar tape",
    "Saddle",
    "Pedals",
    "Sealant",
    "Trainer",
)


class ComponentError(ValueError):
    pass


def local_zone(conn: Connection) -> ZoneInfo:
    """TIMEZONE from the environment, else the latest ride's, else UTC: times typed in forms
    are read in this zone."""
    names = [get_settings().timezone]
    names.append(
        conn.execute(
            select(activity.c.timezone)
            .where(activity.c.timezone.is_not(None))
            .order_by(activity.c.start_at.desc())
            .limit(1)
        ).scalar()
    )
    for name in names:
        if name:
            try:
                return ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return ZoneInfo("UTC")


def parse_local(value: str, zone: ZoneInfo) -> datetime:
    value = value.strip()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ComponentError(f"Not a date: {value!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed


def optional_local(value: str | None, zone: ZoneInfo) -> datetime | None:
    return parse_local(value, zone) if value and value.strip() else None


def _text(value: str | None, limit: int = NAME_MAX) -> str | None:
    value = " ".join((value or "").split())[:limit]
    return value or None


def _translate(error: IntegrityError) -> ComponentError:
    if isinstance(error.orig, errors.ExclusionViolation):
        return ComponentError("It's already on a bike during that period")
    if isinstance(error.orig, errors.CheckViolation):
        return ComponentError("Removal must come after mounting")
    if isinstance(error.orig, errors.ForeignKeyViolation):
        return ComponentError("Still in use, so it can't be deleted")
    return ComponentError(str(error.orig))


def _guarded(conn: Connection, statement):
    """Run a statement in a savepoint and turn constraint violations into ComponentError."""
    try:
        with conn.begin_nested():
            return conn.execute(statement)
    except IntegrityError as error:
        raise _translate(error) from None


def create(
    conn: Connection,
    name: str,
    kind: str = "",
    brand: str | None = None,
    model: str | None = None,
    notes: str | None = None,
) -> int:
    name = _text(name)
    if not name:
        raise ComponentError("Give the component a name")
    return conn.execute(
        insert(component)
        .values(
            name=name,
            kind=_text(kind, 40) or "",
            brand=_text(brand),
            model=_text(model),
            notes=(notes or "").strip() or None,
        )
        .returning(component.c.id)
    ).scalar_one()


def edit(conn: Connection, component_id: int, name: str, kind: str, brand, model, notes) -> None:
    name = _text(name)
    if not name:
        raise ComponentError("Give the component a name")
    conn.execute(
        update(component)
        .where(component.c.id == component_id)
        .values(
            name=name,
            kind=_text(kind, 40) or "",
            brand=_text(brand),
            model=_text(model),
            notes=(notes or "").strip() or None,
        )
    )


def current_assignment(conn: Connection, component_id: int):
    return conn.execute(
        select(component_assignment).where(
            component_assignment.c.component_id == component_id,
            component_assignment.c.removed_at.is_(None),
        )
    ).first()


def mount(conn: Connection, component_id: int, bike_id: int, at: datetime) -> None:
    """Mount on a bike from `at`; if it's on another bike, that period ends at `at`."""
    current = current_assignment(conn, component_id)
    if current is not None:
        if current.bike_id == bike_id:
            raise ComponentError("It's already on that bike")
        if at <= current.mounted_at:
            raise ComponentError("Moving must come after it was mounted on its current bike")
        unmount(conn, component_id, at)
    add_assignment(conn, component_id, bike_id, at, None)


def unmount(conn: Connection, component_id: int, at: datetime) -> None:
    current = current_assignment(conn, component_id)
    if current is None:
        raise ComponentError("It isn't mounted")
    if at <= current.mounted_at:
        raise ComponentError("Removal must come after mounting")
    conn.execute(
        update(component_assignment)
        .where(component_assignment.c.id == current.id)
        .values(removed_at=at)
    )


def retire(conn: Connection, component_id: int, at: datetime) -> None:
    if current_assignment(conn, component_id) is not None:
        unmount(conn, component_id, at)
    conn.execute(update(component).where(component.c.id == component_id).values(retired_at=at))


def unretire(conn: Connection, component_id: int) -> None:
    conn.execute(update(component).where(component.c.id == component_id).values(retired_at=None))


def add_assignment(
    conn: Connection, component_id: int, bike_id: int, mounted_at: datetime, removed_at
) -> None:
    if removed_at is not None and removed_at <= mounted_at:
        raise ComponentError("Removal must come after mounting")
    _guarded(
        conn,
        insert(component_assignment).values(
            component_id=component_id, bike_id=bike_id, mounted_at=mounted_at, removed_at=removed_at
        ),
    )


def edit_assignment(
    conn: Connection, component_id: int, assignment_id: int, bike_id: int, mounted_at, removed_at
) -> None:
    if removed_at is not None and removed_at <= mounted_at:
        raise ComponentError("Removal must come after mounting")
    _guarded(
        conn,
        update(component_assignment)
        .where(
            component_assignment.c.id == assignment_id,
            component_assignment.c.component_id == component_id,
        )
        .values(bike_id=bike_id, mounted_at=mounted_at, removed_at=removed_at),
    )


def delete_assignment(conn: Connection, component_id: int, assignment_id: int) -> None:
    conn.execute(
        delete(component_assignment).where(
            component_assignment.c.id == assignment_id,
            component_assignment.c.component_id == component_id,
        )
    )


DESCRIPTION_MAX = 4000


def _service_values(
    name: str, level, interval_value, interval_unit: str, rides: str, or_days=None, description=None
) -> dict:
    name = _text(name, 80)
    if not name:
        raise ComponentError("Give the service a name")
    try:
        level, interval_value = int(level), float(interval_value)
        or_days = float(or_days) if str(or_days or "").strip() else None
    except (TypeError, ValueError):
        raise ComponentError("Level and intervals must be numbers") from None
    if level < 1 or not interval_value > 0 or (or_days is not None and not or_days > 0):
        raise ComponentError("Level must be 1 or more and intervals above 0")
    if interval_unit not in INTERVAL_UNITS or rides not in RIDE_SCOPES:
        raise ComponentError("Unknown unit or ride scope")
    return {
        "name": name,
        "level": level,
        "interval_value": interval_value,
        "interval_unit": interval_unit,
        "rides": rides,
        "or_days": None if interval_unit == "days" else or_days,
        "description": (description or "").strip()[:DESCRIPTION_MAX] or None,
    }


def add_service_type(
    conn: Connection,
    component_id: int,
    name,
    level,
    interval_value,
    interval_unit,
    rides="all",
    or_days=None,
    description=None,
) -> int:
    values = _service_values(
        name, level, interval_value, interval_unit, rides, or_days, description
    )
    return conn.execute(
        insert(service_type)
        .values(component_id=component_id, **values)
        .returning(service_type.c.id)
    ).scalar_one()


def edit_service_type(
    conn: Connection,
    component_id: int,
    service_type_id: int,
    name,
    level,
    value,
    unit,
    rides,
    or_days=None,
    description=None,
) -> None:
    conn.execute(
        update(service_type)
        .where(
            service_type.c.id == service_type_id,
            service_type.c.component_id == component_id,
        )
        .values(**_service_values(name, level, value, unit, rides, or_days, description))
    )


def delete_service_type(conn: Connection, component_id: int, service_type_id: int) -> None:
    _guarded(
        conn,
        delete(service_type).where(
            service_type.c.id == service_type_id,
            service_type.c.component_id == component_id,
        ),
    )


def copy_schedule(conn: Connection, from_id: int, to_id: int) -> int:
    """Copy another component's service types (skipping names this one already has)."""
    existing = {
        n.lower()
        for n in conn.execute(
            select(service_type.c.name).where(service_type.c.component_id == to_id)
        ).scalars()
    }
    copied = 0
    for row in conn.execute(
        select(service_type).where(service_type.c.component_id == from_id).order_by("id")
    ):
        if row.name.lower() in existing:
            continue
        conn.execute(
            insert(service_type).values(
                component_id=to_id,
                name=row.name,
                level=row.level,
                interval_value=row.interval_value,
                interval_unit=row.interval_unit,
                rides=row.rides,
                or_days=row.or_days,
                description=row.description,
            )
        )
        copied += 1
    return copied


def log_event(
    conn: Connection,
    component_id: int,
    service_type_id: int | None,
    at: datetime,
    notes: str | None = None,
) -> int:
    notes = (notes or "").strip() or None
    if service_type_id is None and not notes:
        raise ComponentError("Pick a service, or write a note")
    return _guarded(
        conn,
        insert(service_event)
        .values(
            component_id=component_id,
            service_type_id=service_type_id,
            performed_at=at,
            notes=notes,
        )
        .returning(service_event.c.id),
    ).scalar_one()


def delete_event(conn: Connection, component_id: int, event_id: int) -> None:
    conn.execute(
        delete(service_event).where(
            service_event.c.id == event_id, service_event.c.component_id == component_id
        )
    )


def delete_component(conn: Connection, component_id: int) -> None:
    conn.execute(delete(component).where(component.c.id == component_id))


INDOOR = "(r.trainer OR r.type = 'VirtualRide')"

USAGE = text(
    f"""
    SELECT t.id, t.component_id, t.name, t.level, t.interval_value, t.interval_unit, t.rides,
           t.or_days, t.description, reset.at AS reset_at,
           used.rides AS ride_count, used.km, used.hours,
           CASE t.interval_unit
             WHEN 'km' THEN used.km
             WHEN 'hours' THEN used.hours
             ELSE age.days
           END AS used,
           age.days
    FROM service_type t
    CROSS JOIN LATERAL (
      SELECT max(e.performed_at) AS at
      FROM service_event e JOIN service_type et ON et.id = e.service_type_id
      WHERE e.component_id = t.component_id AND et.level >= t.level
    ) reset
    CROSS JOIN LATERAL (
      SELECT min(a.mounted_at) AS at FROM component_assignment a
      WHERE a.component_id = t.component_id
    ) first
    CROSS JOIN LATERAL (
      SELECT extract(epoch FROM (:now - coalesce(reset.at, first.at))) / 86400 AS days
    ) age
    CROSS JOIN LATERAL (
      SELECT count(r.id) AS rides,
             coalesce(sum(r.distance_m), 0) / 1000.0 AS km,
             coalesce(sum(r.moving_time_s), 0) / 3600.0 AS hours
      FROM component_assignment a
      JOIN activity r ON r.bike_id = a.bike_id
        AND r.start_at >= a.mounted_at
        AND (a.removed_at IS NULL OR r.start_at < a.removed_at)
        AND (reset.at IS NULL OR r.start_at > reset.at)
        AND (t.rides = 'all' OR {INDOOR} = (t.rides = 'indoor'))
      WHERE a.component_id = t.component_id
    ) used
    WHERE CAST(:ids AS integer[]) IS NULL OR t.component_id = ANY(CAST(:ids AS integer[]))
    ORDER BY t.component_id, t.level, t.name
    """
)


@dataclass(frozen=True)
class Usage:
    id: int
    component_id: int
    name: str
    level: int
    interval_value: float
    interval_unit: str
    rides: str
    or_days: float | None
    description: str | None
    reset_at: datetime | None
    ride_count: int
    km: float
    hours: float
    used: float | None
    days: float | None

    @property
    def days_fraction(self) -> float | None:
        return None if self.or_days is None or self.days is None else self.days / self.or_days

    @property
    def by_days(self) -> bool:
        """The "or N days" limit is the one closer to due."""
        return self.days_fraction is not None and self.days_fraction > (self.fraction_used or 0)

    @property
    def fraction_used(self) -> float | None:
        return None if self.used is None else self.used / self.interval_value

    @property
    def fraction(self) -> float | None:
        return self.days_fraction if self.by_days else self.fraction_used

    @property
    def status(self) -> str:
        fraction = self.fraction or 0
        return "over" if fraction >= 1 else "soon" if fraction >= DUE_SOON else "ok"


def usage(
    conn: Connection, component_ids: list[int] | None = None, now: datetime | None = None
) -> list[Usage]:
    found = []
    for r in conn.execute(USAGE, {"ids": component_ids, "now": now or datetime.now(UTC)}):
        values = dict(r._mapping)
        for key in ("km", "hours", "used", "days"):
            values[key] = None if values[key] is None else float(values[key])
        found.append(Usage(**values))
    return found


LIFETIME = text(
    """
    SELECT a.component_id, count(r.id) AS rides,
           coalesce(sum(r.distance_m), 0) AS distance_m,
           coalesce(sum(r.moving_time_s), 0) AS moving_time_s
    FROM component_assignment a
    JOIN activity r ON r.bike_id = a.bike_id AND r.start_at >= a.mounted_at
      AND (a.removed_at IS NULL OR r.start_at < a.removed_at)
    GROUP BY a.component_id
    """
)


def lifetime(conn: Connection) -> dict[int, object]:
    """Every ride a component has done, on every bike, indoor or not."""
    return {r.component_id: r for r in conn.execute(LIFETIME)}


def mounted_on_ride(conn: Connection, activity_id: int):
    a, r = component_assignment, activity
    return conn.execute(
        select(component.c.id, component.c.name, component.c.kind)
        .join(a, a.c.component_id == component.c.id)
        .join(r, r.c.bike_id == a.c.bike_id)
        .where(
            r.c.id == activity_id,
            r.c.start_at >= a.c.mounted_at,
            (a.c.removed_at.is_(None)) | (r.c.start_at < a.c.removed_at),
        )
        .order_by(component.c.kind, component.c.name)
    ).all()


def bikes(conn: Connection):
    return conn.execute(select(bike).order_by(bike.c.retired, bike.c.name)).all()


def listing(conn: Connection):
    """Every component with its current bike (if mounted) and since when."""
    a = component_assignment
    return conn.execute(
        select(
            component,
            a.c.bike_id,
            a.c.mounted_at,
            bike.c.name.label("bike_name"),
        )
        .select_from(
            component.outerjoin(
                a, (a.c.component_id == component.c.id) & a.c.removed_at.is_(None)
            ).outerjoin(bike, bike.c.id == a.c.bike_id)
        )
        .order_by(component.c.kind, component.c.name, component.c.id)
    ).all()


def detail(conn: Connection, component_id: int):
    return next((c for c in listing(conn) if c.id == component_id), None)


def history(conn: Connection, component_id: int):
    a = component_assignment
    return conn.execute(
        select(a, bike.c.name.label("bike_name"))
        .join(bike, bike.c.id == a.c.bike_id)
        .where(a.c.component_id == component_id)
        .order_by(a.c.mounted_at.desc())
    ).all()


def events(conn: Connection, component_id: int):
    e, t = service_event, service_type
    return conn.execute(
        select(e, t.c.name.label("service_name"), t.c.level)
        .outerjoin(t, t.c.id == e.c.service_type_id)
        .where(e.c.component_id == component_id)
        .order_by(e.c.performed_at.desc(), e.c.id.desc())
    ).all()


def kinds(conn: Connection) -> list[str]:
    used = conn.execute(select(component.c.kind).distinct().where(component.c.kind != "")).scalars()
    return sorted({*KINDS, *used}, key=str.lower)


def with_schedule(conn: Connection):
    """Components that have service types, to copy a schedule from."""
    return conn.execute(
        select(component.c.id, component.c.name, component.c.kind)
        .where(
            select(service_type.c.id).where(service_type.c.component_id == component.c.id).exists()
        )
        .order_by(component.c.kind, component.c.name)
    ).all()


def rides_without_bike(conn: Connection, limit: int = 20):
    """How many rides have no bike; the latest outdoor ones."""
    r = activity
    indoor = r.c.trainer | (r.c.type == "VirtualRide")
    counts = conn.execute(
        select(func.count(), func.count().filter(indoor)).where(r.c.bike_id.is_(None))
    ).one()
    latest = conn.execute(
        select(r.c.id, r.c.name, r.c.type, r.c.start_at_local, r.c.distance_m)
        .where(r.c.bike_id.is_(None), ~indoor)
        .order_by(r.c.start_at.desc())
        .limit(limit)
    ).all()
    return {"count": counts[0], "indoor": counts[1], "latest": latest}
