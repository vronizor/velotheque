import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Connection, delete, select, text, union
from sqlalchemy.dialects.postgresql import distinct_on, insert

from velotheque.companions import link_mentions
from velotheque.config import Settings
from velotheque.exportrow import as_api_payload
from velotheque.geo import multilinestring_wkt, split_segments
from velotheque.tables import (
    activity,
    activity_track,
    bike,
    raw_export_activity,
    raw_strava_activity,
    raw_strava_athlete,
    raw_strava_gear,
    raw_strava_streams,
)
from velotheque.trackfiles import parse_track_file

log = logging.getLogger(__name__)
# Strava mentions: "[strava://athletes/<id>/<length>]Name" → "Name".
MENTION = re.compile(r"\[strava://[^\]\s]*\]")
_TZ_NAME = re.compile(r"\)\s*(\S+)\s*$")


def parse_timezone(value: str | None) -> str | None:
    if not value:
        return None
    match = _TZ_NAME.search(value)
    return match.group(1) if match else value


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def parse_local(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z")).replace(tzinfo=None)


def _latest(conn: Connection, table, **key):
    query = select(table.c.id, table.c.payload)
    for name, value in key.items():
        query = query.where(table.c[name] == value)
    return conn.execute(query.order_by(table.c.id.desc()).limit(1)).first()


def _gear_name(payload: dict) -> str:
    return payload.get("nickname") or payload.get("name") or payload["id"]


def _upsert_bike(conn: Connection, gear_id: str, name: str, retired: bool | None) -> None:
    values = {"strava_gear_id": gear_id, "name": name}
    if retired is not None:
        values["retired"] = retired
    stmt = insert(bike).values(**values)
    conn.execute(stmt.on_conflict_do_update(index_elements=[bike.c.strava_gear_id], set_=values))


def normalize_bikes(conn: Connection) -> None:
    athlete = _latest(conn, raw_strava_athlete)
    if athlete:
        for gear in athlete.payload.get("bikes") or []:
            _upsert_bike(conn, gear["id"], _gear_name(gear), gear.get("retired"))
    latest_gear = conn.execute(
        select(raw_strava_gear.c.payload)
        .ext(distinct_on(raw_strava_gear.c.source_id))
        .order_by(raw_strava_gear.c.source_id, raw_strava_gear.c.id.desc())
    ).scalars()
    for gear in latest_gear:
        _upsert_bike(conn, gear["id"], _gear_name(gear), gear.get("retired"))


def _bike_id(conn: Connection, gear_id: str | None, name_hint: str | None = None) -> int | None:
    if not gear_id:
        if not name_hint:
            return None
        return conn.execute(select(bike.c.id).where(bike.c.name == name_hint).limit(1)).scalar()
    existing = conn.execute(select(bike.c.id).where(bike.c.strava_gear_id == gear_id)).scalar()
    if existing is not None:
        return existing
    return conn.execute(
        insert(bike).values(strava_gear_id=gear_id, name=name_hint or gear_id).returning(bike.c.id)
    ).scalar_one()


def merged_payload(summary, detail) -> dict | None:
    if summary is None and detail is None:
        return None
    if detail is None:
        return summary.payload
    if summary is None or detail.id > summary.id:
        return detail.payload
    return {**detail.payload, **summary.payload}


def _latest_export(conn: Connection, source_id: int):
    return conn.execute(
        select(raw_export_activity.c.payload, raw_export_activity.c.file_path)
        .where(raw_export_activity.c.source_id == source_id)
        .order_by(raw_export_activity.c.id.desc())
        .limit(1)
    ).first()


def normalize_activity(
    conn: Connection, source_id: int, settings: Settings, force_track: bool = False
) -> int | None:
    """API data wins for metadata; the bulk export fills in when the API has nothing."""
    summary = _latest(conn, raw_strava_activity, source_id=source_id, kind="summary")
    detail = _latest(conn, raw_strava_activity, source_id=source_id, kind="detail")
    export = _latest_export(conn, source_id)
    exported = as_api_payload(export.payload) if export else {}
    payload = merged_payload(summary, detail) or exported
    if not payload:
        return None
    sport = payload.get("sport_type") or payload.get("type")
    if sport not in settings.sport_types:
        conn.execute(
            delete(activity).where(activity.c.source == "strava", activity.c.source_id == source_id)
        )
        return None

    raw_description = detail.payload.get("description") if detail else exported.get("description")
    description = MENTION.sub("", raw_description) if raw_description else None
    photos = max(payload.get("total_photo_count") or 0, exported.get("total_photo_count") or 0)
    values = {
        "name": payload.get("name") or "",
        "description": description or None,
        "type": sport,
        "start_at": parse_utc(payload["start_date"]),
        "start_at_local": parse_local(payload["start_date_local"]),
        "timezone": parse_timezone(payload.get("timezone")),
        "distance_m": payload.get("distance") or 0,
        "moving_time_s": payload.get("moving_time") or 0,
        "elapsed_time_s": payload.get("elapsed_time") or 0,
        "elevation_gain_m": payload.get("total_elevation_gain") or 0,
        "bike_id": _bike_id(conn, payload.get("gear_id"), exported.get("gear_name")),
        "trainer": bool(payload.get("trainer")),
        "has_photos": photos > 0,
        "athlete_count": payload.get("athlete_count"),
        "synced_at": datetime.now(UTC),
    }
    stmt = insert(activity).values(source="strava", source_id=source_id, **values)
    activity_id = conn.execute(
        stmt.on_conflict_do_update(
            constraint="uq_activity_source_source_id", set_=values
        ).returning(activity.c.id)
    ).scalar_one()
    link_mentions(conn, activity_id, raw_description)
    export_file = settings.data_dir / export.file_path if export and export.file_path else None
    _normalize_track(conn, activity_id, source_id, payload, export_file, force_track)
    return activity_id


def _upsert_track(conn: Connection, activity_id: int, geometry_sql: str, params: dict, **cols):
    columns = [
        "activity_id",
        "geometry",
        "track_source",
        "time_s",
        "distance_m",
        "altitude_m",
        "moving",
    ]
    values = {"time_s": None, "distance_m": None, "altitude_m": None, "moving": None, **cols}
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns[1:])
    conn.execute(
        text(
            f"""
            INSERT INTO activity_track ({", ".join(columns)})
            SELECT CAST(:activity_id AS bigint), g, :track_source,
                   CAST(:time_s AS integer[]), CAST(:distance_m AS real[]),
                   CAST(:altitude_m AS real[]), CAST(:moving AS boolean[])
            FROM (SELECT {geometry_sql} AS g) AS t
            WHERE ST_NPoints(g) >= 2
            ON CONFLICT (activity_id) DO UPDATE SET {set_clause}
            """
        ).bindparams(activity_id=activity_id, **params, **values)
    )


def _full_track(conn: Connection, activity_id: int, latlng, track_source: str, **arrays) -> bool:
    segments = split_segments(latlng or [])
    if not segments:
        return False
    _upsert_track(
        conn,
        activity_id,
        "ST_GeomFromText(:wkt, 4326)",
        {"wkt": multilinestring_wkt(segments)},
        track_source=track_source,
        **arrays,
    )
    return True


def _streams_track(conn: Connection, activity_id: int, streams: dict) -> bool:
    def data(key):
        return (streams.get(key) or {}).get("data")

    return _full_track(
        conn,
        activity_id,
        data("latlng"),
        "streams",
        time_s=data("time"),
        distance_m=data("distance"),
        altitude_m=data("altitude"),
        moving=data("moving"),
    )


def _export_track(conn: Connection, activity_id: int, path: Path) -> bool:
    try:
        track = parse_track_file(path)
    except Exception:
        log.warning("could not parse export track %s", path, exc_info=True)
        return False
    return _full_track(
        conn,
        activity_id,
        track.latlng,
        "export",
        time_s=track.time_s(),
        distance_m=track.distances(),
        altitude_m=track.altitudes(),
    )


def _normalize_track(
    conn: Connection,
    activity_id: int,
    source_id: int,
    payload: dict,
    export_file: Path | None,
    force: bool,
) -> None:
    """Track priority: API streams, then the export's track file, then Strava's polyline."""
    streams = conn.execute(
        select(raw_strava_streams.c.http_status, raw_strava_streams.c.payload)
        .where(raw_strava_streams.c.source_id == source_id)
        .order_by(raw_strava_streams.c.id.desc())
        .limit(1)
    ).first()
    if (
        streams
        and streams.http_status == 200
        and isinstance(streams.payload, dict)
        and _streams_track(conn, activity_id, streams.payload)
    ):
        return

    current = conn.execute(
        select(activity_track.c.track_source).where(activity_track.c.activity_id == activity_id)
    ).scalar()
    if export_file is not None:
        if current == "export" and not force:
            return
        if export_file.exists() and _export_track(conn, activity_id, export_file):
            return
    if current in ("streams", "export"):
        return

    route_map = payload.get("map") or {}
    polyline = route_map.get("polyline") or route_map.get("summary_polyline")
    if polyline:
        _upsert_track(
            conn,
            activity_id,
            "ST_Multi(ST_LineFromEncodedPolyline(:polyline))",
            {"polyline": polyline},
            track_source="polyline",
        )
    elif current == "polyline":
        conn.execute(delete(activity_track).where(activity_track.c.activity_id == activity_id))


def normalize_all(conn: Connection, settings: Settings, force_track: bool = False) -> list[int]:
    normalize_bikes(conn)
    source_ids = (
        conn.execute(
            union(select(raw_strava_activity.c.source_id), select(raw_export_activity.c.source_id))
        )
        .scalars()
        .all()
    )
    ids = [normalize_activity(conn, sid, settings, force_track) for sid in source_ids]
    return [i for i in ids if i is not None]
