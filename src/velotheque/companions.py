"""People ridden with. Strava's API never says who was in a group ride (only how many), so
they come from mentions in the description or are added by hand."""

import re

from sqlalchemy import BigInteger, Connection, any_, cast, delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import ARRAY, insert

from velotheque.tables import activity_companion, companion

# "[strava://athletes/<id>/<length>]Name": the name is the <length> characters that follow.
MENTION = re.compile(r"\[strava://athletes/(\d+)/(\d+)\]")
NAME_MAX = 80


def mentions(description: str | None) -> list[tuple[int, str]]:
    found = []
    for m in MENTION.finditer(description or ""):
        name = description[m.end() : m.end() + int(m.group(2))].strip()
        if name:
            found.append((int(m.group(1)), name))
    return found


def clean_name(name: str) -> str:
    return " ".join(name.split())[:NAME_MAX]


def find(conn: Connection, name: str, exclude: int | None = None) -> int | None:
    query = select(companion.c.id).where(func.lower(companion.c.name) == clean_name(name).lower())
    if exclude is not None:
        query = query.where(companion.c.id != exclude)
    return conn.execute(query.order_by(companion.c.id).limit(1)).scalar()


def _for_athlete(conn: Connection, athlete_id: int, name: str) -> int:
    """The person a mention points to: known by their Strava id, else by name (someone added
    by hand before they were mentioned), else new."""
    known = conn.execute(
        select(companion.c.id)
        .where(literal(athlete_id) == any_(companion.c.strava_athlete_ids))
        .limit(1)
    ).scalar()
    if known is not None:
        return known
    by_name = conn.execute(
        select(companion.c.id)
        .where(
            func.lower(companion.c.name) == clean_name(name).lower(),
            func.cardinality(companion.c.strava_athlete_ids) == 0,
        )
        .limit(1)
    ).scalar()
    if by_name is not None:
        conn.execute(
            update(companion)
            .where(companion.c.id == by_name)
            .values(strava_athlete_ids=[athlete_id])
        )
        return by_name
    return conn.execute(
        companion.insert()
        .values(name=clean_name(name), strava_athlete_ids=[athlete_id])
        .returning(companion.c.id)
    ).scalar_one()


def link_mentions(conn: Connection, activity_id: int, description: str | None) -> None:
    for athlete_id, name in mentions(description):
        stmt = insert(activity_companion).values(
            activity_id=activity_id,
            companion_id=_for_athlete(conn, athlete_id, name),
            source="mention",
        )
        conn.execute(stmt.on_conflict_do_nothing())


def add(conn: Connection, activity_id: int, name: str) -> int | None:
    name = clean_name(name)
    if not name:
        return None
    companion_id = find(conn, name)
    if companion_id is None:
        companion_id = conn.execute(
            companion.insert().values(name=name).returning(companion.c.id)
        ).scalar_one()
    stmt = insert(activity_companion).values(
        activity_id=activity_id, companion_id=companion_id, source="manual"
    )
    conn.execute(
        stmt.on_conflict_do_update(constraint="pk_activity_companion", set_={"hidden": False})
    )
    return companion_id


def remove(conn: Connection, activity_id: int, companion_id: int) -> None:
    """A hand-added link goes; a mention is only hidden, so it stays gone after the next sync.
    Someone left on no ride at all is forgotten."""
    link = (activity_companion.c.activity_id == activity_id) & (
        activity_companion.c.companion_id == companion_id
    )
    conn.execute(delete(activity_companion).where(link, activity_companion.c.source == "manual"))
    conn.execute(update(activity_companion).where(link).values(hidden=True))
    conn.execute(
        delete(companion).where(
            companion.c.id == companion_id,
            ~select(activity_companion.c.activity_id)
            .where(activity_companion.c.companion_id == companion_id)
            .exists(),
        )
    )


def rename(conn: Connection, companion_id: int, name: str) -> int:
    """Rename; a name someone else already has merges the two (rides and Strava ids)."""
    name = clean_name(name)
    if not name:
        return companion_id
    other = find(conn, name, exclude=companion_id)
    if other is None:
        conn.execute(update(companion).where(companion.c.id == companion_id).values(name=name))
        return companion_id
    links = activity_companion
    moved = select(links.c.activity_id, literal(other), links.c.source, links.c.hidden).where(
        links.c.companion_id == companion_id
    )
    conn.execute(
        insert(links)
        .from_select(["activity_id", "companion_id", "source", "hidden"], moved)
        .on_conflict_do_nothing()
    )
    athlete_ids = conn.execute(
        delete(companion)
        .where(companion.c.id == companion_id)
        .returning(companion.c.strava_athlete_ids)
    ).scalar_one()
    conn.execute(
        update(companion)
        .where(companion.c.id == other)
        .values(
            strava_athlete_ids=func.array_cat(
                companion.c.strava_athlete_ids, cast(athlete_ids, ARRAY(BigInteger))
            )
        )
    )
    return other
