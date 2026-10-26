from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from tests.fake_strava import SETTINGS, streams_for, summary, sync
from velotheque.strava import oauth
from velotheque.strava.sync import JobAlreadyRunning, start_job
from velotheque.tables import (
    activity,
    activity_track,
    activity_track_simplified,
    bike,
    raw_strava_activity,
    raw_strava_streams,
    strava_auth,
)


def count(db, table, *where):
    with db.connect() as conn:
        return conn.execute(select(func.count()).select_from(table).where(*where)).scalar()


def test_first_sync_builds_activities_tracks_and_bikes(db, strava):
    strava.add(summary(1, day=0))
    strava.add(summary(2, day=1, gear_id="b2"))
    strava.add(summary(3, day=2, sport_type="Run", type="Run", gear_id="g9"))
    strava.add(summary(4, day=3, sport_type="VirtualRide", map={"summary_polyline": ""}), None)
    strava.add(summary(5, day=4, sport_type="Swim", type="Swim", gear_id=None))
    strava.descriptions[1] = "Col de la Croix"

    job = sync(db, strava)

    assert job.status == "succeeded", job.error
    assert count(db, activity) == 4
    assert count(db, raw_strava_activity, raw_strava_activity.c.kind == "summary") == 5
    assert count(db, activity_track, activity_track.c.track_source == "streams") == 3
    assert count(db, activity_track_simplified) == 6
    with db.connect() as conn:
        bikes = dict(conn.execute(select(bike.c.strava_gear_id, bike.c.name)).all())
        ride = conn.execute(select(activity).where(activity.c.source_id == 1)).one()
    assert bikes == {"b1": "Gravel", "b2": "Old road bike"}
    assert ride.description == "Col de la Croix"
    assert ride.timezone == "Europe/Paris"
    assert ride.start_at_local == datetime(2026, 9, 1, 10, 0)
    assert "/api/v3/activities/3" in strava.calls
    assert "/api/v3/activities/5" not in strava.calls
    assert not [c for c in strava.calls if "/gear/" in c and "g9" in c]


def test_resync_is_idempotent(db, strava):
    strava.add(summary(1))
    strava.add(summary(2, day=1))
    sync(db, strava)
    raw_before = count(db, raw_strava_activity), count(db, raw_strava_streams)
    strava.calls.clear()

    job = sync(db, strava)

    assert job.status == "succeeded"
    assert (count(db, raw_strava_activity), count(db, raw_strava_streams)) == raw_before
    assert count(db, activity) == 2
    assert [c for c in strava.calls if "/activities/" in c] == []


def test_kudos_change_is_ignored_but_rename_refetches(db, strava):
    strava.add(summary(1))
    sync(db, strava)

    strava.activities[1]["kudos_count"] = 7
    strava.calls.clear()
    sync(db, strava)
    assert [c for c in strava.calls if "/activities/1" in c] == []

    strava.activities[1]["name"] = "Renamed"
    strava.calls.clear()
    sync(db, strava)
    assert "/api/v3/activities/1" in strava.calls
    assert "/api/v3/activities/1/streams" in strava.calls
    with db.connect() as conn:
        assert conn.execute(select(activity.c.name)).scalar() == "Renamed"

    strava.calls.clear()
    sync(db, strava)
    assert [c for c in strava.calls if "/activities/1" in c] == []


def test_rate_limit_stops_cleanly_and_next_run_resumes(db, strava):
    for i in range(1, 4):
        strava.add(summary(i, day=i))
    strava.read_limit = (10, 1000)

    job = sync(db, strava)

    assert job.status == "rate_limited"
    assert job.resumes_after is not None
    assert count(db, raw_strava_streams) < 3
    assert count(db, activity_track) == 3

    strava.read_usage = [0, 0]
    job = sync(db, strava)

    assert job.status == "succeeded", job.error
    assert count(db, raw_strava_streams) == 3
    assert count(db, activity_track, activity_track.c.track_source == "streams") == 3


def test_missing_streams_are_stored_and_not_retried(db, strava):
    strava.add(summary(1), None)
    sync(db, strava)
    with db.connect() as conn:
        status = conn.execute(select(raw_strava_streams.c.http_status)).scalar()
        source = conn.execute(select(activity_track.c.track_source)).scalar()
    assert status == 404
    assert source == "polyline"

    strava.calls.clear()
    sync(db, strava)
    assert "/api/v3/activities/1/streams" not in strava.calls


def test_gps_gap_splits_track(db, strava):
    strava.add(summary(1), streams_for(40, gap_at=20))
    sync(db, strava)
    with db.connect() as conn:
        parts = conn.execute(text("SELECT ST_NumGeometries(geometry) FROM activity_track")).scalar()
    assert parts == 2


def test_expired_token_is_refreshed_and_rotation_persisted(db, strava):
    with db.begin() as conn:
        conn.execute(
            strava_auth.update().values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    strava.add(summary(1))

    job = sync(db, strava)

    assert job.status == "succeeded", job.error
    assert strava.tokens_issued == 1
    with db.connect() as conn:
        assert oauth.load_auth(conn).refresh_token == "refresh-1"


def test_refresh_job_picks_up_strava_edit(db, strava):
    strava.add(summary(1))
    sync(db, strava)
    strava.activities[1]["name"] = "Fixed title"
    strava.descriptions[1] = "now with notes"

    job = sync(db, strava, kind="refresh", source_id=1)

    assert job.status == "succeeded", job.error
    with db.connect() as conn:
        row = conn.execute(select(activity.c.name, activity.c.description)).one()
    assert tuple(row) == ("Fixed title", "now with notes")


def test_only_one_job_runs_at_a_time(db):
    start_job(db, "sync")
    with pytest.raises(JobAlreadyRunning):
        start_job(db, "sync")


def _snapshot(db):
    with db.connect() as conn:
        tracks = conn.execute(
            text(
                "SELECT activity_id, level, ST_AsText(geometry) FROM activity_track_simplified "
                "ORDER BY 1, 2"
            )
        ).all()
        rides = conn.execute(
            select(activity.c.source_id, activity.c.name, activity.c.bike_id).order_by(
                activity.c.source_id
            )
        ).all()
    return tracks, rides


def test_rebuild_derived_and_normalize_all_are_repeatable(db, strava):
    from velotheque.derive import derive_activities, rebuild_derived
    from velotheque.normalize import normalize_all

    strava.add(summary(1))
    strava.add(summary(2, day=1), None)
    sync(db, strava)
    before = _snapshot(db)

    for _ in range(2):
        with db.begin() as conn:
            rebuild_derived(conn)
        with db.begin() as conn:
            derive_activities(conn, normalize_all(conn, SETTINGS))
        assert _snapshot(db) == before
