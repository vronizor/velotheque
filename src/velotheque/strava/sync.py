import logging
from datetime import timedelta

from sqlalchemy import Connection, Engine, func, select, text, update
from sqlalchemy.exc import IntegrityError

from velotheque.config import Settings
from velotheque.derive import derive_activities
from velotheque.normalize import normalize_activity, normalize_bikes
from velotheque.photos import materialize_photos
from velotheque.raw import store_raw
from velotheque.strava.client import StravaClient
from velotheque.strava.oauth import NotConnected
from velotheque.strava.ratelimit import RateLimited
from velotheque.tables import (
    bike,
    raw_strava_activity,
    raw_strava_athlete,
    raw_strava_gear,
    raw_strava_streams,
    sync_job,
)

log = logging.getLogger(__name__)

PER_PAGE = 200
SIGNATURE_FIELDS = (
    "name",
    "distance",
    "gear_id",
    "sport_type",
    "moving_time",
    "elapsed_time",
    "total_elevation_gain",
    "start_date",
)


def _signature(alias: str) -> str:
    fields = ", ".join(f"{alias}.payload->'{f}'" for f in SIGNATURE_FIELDS)
    return f"jsonb_build_array({fields})"


PENDING = text(
    f"""
    WITH latest_summary AS (
        SELECT DISTINCT ON (source_id) id, source_id, payload
        FROM raw_strava_activity WHERE kind = 'summary'
        ORDER BY source_id, id DESC
    ), latest_detail AS (
        SELECT DISTINCT ON (source_id) id, source_id
        FROM raw_strava_activity WHERE kind = 'detail'
        ORDER BY source_id, id DESC
    ), state AS (
        SELECT s.source_id,
               s.payload->>'start_date' AS start_date,
               d.id IS NULL AND NOT EXISTS (
                   SELECT 1 FROM raw_export_activity e WHERE e.source_id = s.source_id
               ) AS no_detail,
               d.id IS NOT NULL
                   AND {_signature("s")} IS DISTINCT FROM {_signature("prev")} AS changed,
               NOT EXISTS (
                   SELECT 1 FROM raw_strava_streams st WHERE st.source_id = s.source_id
               ) AND NOT EXISTS (
                   SELECT 1 FROM raw_export_activity e
                   WHERE e.source_id = s.source_id AND e.file_path IS NOT NULL
               ) AS no_streams
        FROM latest_summary s
        LEFT JOIN latest_detail d USING (source_id)
        LEFT JOIN LATERAL (
            SELECT p.payload FROM raw_strava_activity p
            WHERE p.source_id = s.source_id AND p.kind = 'summary' AND p.id < d.id
            ORDER BY p.id DESC LIMIT 1
        ) prev ON true
        WHERE coalesce(s.payload->>'sport_type', s.payload->>'type') = ANY(:sport_types)
    )
    SELECT source_id, no_detail OR changed AS need_detail, no_streams OR changed AS need_streams
    FROM state
    WHERE no_detail OR changed OR no_streams
    ORDER BY start_date DESC
    """
)


class JobAlreadyRunning(Exception):
    pass


def start_job(engine: Engine, kind: str, activity_source_id: int | None = None) -> int:
    try:
        with engine.begin() as conn:
            return conn.execute(
                sync_job.insert()
                .values(kind=kind, activity_source_id=activity_source_id)
                .returning(sync_job.c.id)
            ).scalar_one()
    except IntegrityError as exc:
        raise JobAlreadyRunning("A sync job is already running") from exc


def mark_interrupted(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(sync_job)
            .where(sync_job.c.status == "running")
            .values(status="interrupted", finished_at=func.now())
        )


def _save_counts(engine: Engine, job_id: int, counts: dict) -> None:
    with engine.begin() as conn:
        conn.execute(update(sync_job).where(sync_job.c.id == job_id).values(counts=counts))


def _finish(engine: Engine, job_id: int, counts: dict, status: str, **values) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(sync_job)
            .where(sync_job.c.id == job_id)
            .values(status=status, counts=counts, finished_at=func.now(), **values)
        )


def list_after(conn: Connection, overlap_days: int) -> int:
    last = conn.execute(
        select(sync_job.c.counts)
        .where(sync_job.c.kind == "sync", sync_job.c.counts.has_key("list_after"))
        .order_by(sync_job.c.id.desc())
        .limit(1)
    ).scalar()
    if last and not last.get("list_complete"):
        return last["list_after"]
    latest = conn.execute(
        text(
            "SELECT max((payload->>'start_date')::timestamptz) FROM raw_strava_activity "
            "WHERE kind = 'summary'"
        )
    ).scalar()
    if latest is None:
        return 0
    return int((latest - timedelta(days=overlap_days)).timestamp())


def _ingest(engine: Engine, settings: Settings, source_id: int, http=None) -> None:
    with engine.begin() as conn:
        activity_id = normalize_activity(conn, source_id, settings)
        if activity_id is not None:
            derive_activities(conn, [activity_id])
            materialize_photos(conn, activity_id, source_id, settings, http)


def _sync_athlete(engine: Engine, client: StravaClient) -> None:
    athlete = client.athlete()
    with engine.begin() as conn:
        store_raw(conn, raw_strava_athlete, athlete, {"source_id": athlete["id"]})
        normalize_bikes(conn)


def _sync_unknown_gear(engine: Engine, client: StravaClient, counts: dict) -> None:
    with engine.connect() as conn:
        unknown = (
            conn.execute(
                select(bike.c.strava_gear_id).where(
                    bike.c.name == bike.c.strava_gear_id,
                    ~select(raw_strava_gear.c.id)
                    .where(raw_strava_gear.c.source_id == bike.c.strava_gear_id)
                    .exists(),
                )
            )
            .scalars()
            .all()
        )
    for gear_id in unknown:
        gear = client.gear(gear_id)
        with engine.begin() as conn:
            store_raw(conn, raw_strava_gear, gear, {"source_id": gear_id})
            normalize_bikes(conn)
        counts["gear"] = counts.get("gear", 0) + 1


def _list_activities(engine: Engine, settings: Settings, client: StravaClient, job_id, counts):
    with engine.connect() as conn:
        after = list_after(conn, settings.sync_overlap_days)
    counts.update(list_after=after, list_complete=False, listed=0, new_or_changed=0)
    _save_counts(engine, job_id, counts)
    page = 1
    while True:
        summaries = client.list_activities(after, page, PER_PAGE)
        for summary in summaries:
            with engine.begin() as conn:
                changed = store_raw(
                    conn,
                    raw_strava_activity,
                    summary,
                    {"source_id": summary["id"], "kind": "summary"},
                )
            if changed:
                counts["new_or_changed"] += 1
                _ingest(engine, settings, summary["id"])
            counts["listed"] += 1
            if counts["listed"] % 25 == 0:
                _save_counts(engine, job_id, counts)
        if len(summaries) < PER_PAGE:
            break
        page += 1
    counts["list_complete"] = True
    _save_counts(engine, job_id, counts)


def fetch_activity(
    engine: Engine,
    settings: Settings,
    client: StravaClient,
    source_id: int,
    detail: bool = True,
    streams: bool = True,
) -> None:
    if detail:
        payload = client.activity(source_id)
        with engine.begin() as conn:
            store_raw(
                conn, raw_strava_activity, payload, {"source_id": source_id, "kind": "detail"}
            )
    if streams:
        status, body = client.streams(source_id)
        with engine.begin() as conn:
            store_raw(
                conn,
                raw_strava_streams,
                body,
                {"source_id": source_id},
                {"http_status": status},
            )
    _ingest(engine, settings, source_id, client.http)


def pending(conn: Connection, sport_types) -> list:
    return conn.execute(PENDING, {"sport_types": list(sport_types)}).all()


def _fetch_pending(engine: Engine, settings: Settings, client: StravaClient, job_id, counts):
    with engine.connect() as conn:
        queue = pending(conn, settings.sport_types)
    counts.update(pending=len(queue), fetched=0)
    _save_counts(engine, job_id, counts)
    for row in queue:
        fetch_activity(engine, settings, client, row.source_id, row.need_detail, row.need_streams)
        counts["fetched"] += 1
        if counts["fetched"] % 10 == 0:
            _save_counts(engine, job_id, counts)


def run_job(engine: Engine, settings: Settings, client: StravaClient, job_id: int) -> None:
    with engine.connect() as conn:
        job = conn.execute(select(sync_job).where(sync_job.c.id == job_id)).one()
    counts: dict = {}
    try:
        if job.kind == "refresh":
            fetch_activity(engine, settings, client, job.activity_source_id)
        else:
            _sync_athlete(engine, client)
            _list_activities(engine, settings, client, job_id, counts)
            _sync_unknown_gear(engine, client, counts)
            _fetch_pending(engine, settings, client, job_id, counts)
    except RateLimited as exc:
        _finish(engine, job_id, counts, "rate_limited", resumes_after=exc.resumes_after)
    except NotConnected as exc:
        _finish(engine, job_id, counts, "failed", error=str(exc))
    except Exception as exc:
        log.exception("sync job %s failed", job_id)
        _finish(engine, job_id, counts, "failed", error=f"{type(exc).__name__}: {exc}")
    else:
        _finish(engine, job_id, counts, "succeeded")
