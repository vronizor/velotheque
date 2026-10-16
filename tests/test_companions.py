from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import select, text

from tests.fake_strava import SETTINGS, summary, sync
from velotheque import companions
from velotheque.cli import alembic_config
from velotheque.filters import Entity, FilterState, build_where, facet_counts
from velotheque.queries import companions as all_companions
from velotheque.queries import ride_companions
from velotheque.tables import activity, activity_companion, companion


def ride_id(conn, source_id: int) -> int:
    return conn.execute(select(activity.c.id).where(activity.c.source_id == source_id)).scalar()


def names(conn, source_id: int) -> list[str]:
    return [c.name for c in ride_companions(conn, ride_id(conn, source_id))]


def test_mentions_read_the_announced_length():
    found = companions.mentions(
        "Avec [strava://athletes/7/10]Zoé Durand, Jeff et [strava://athletes/8/3]Sam."
    )
    assert found == [(7, "Zoé Durand"), (8, "Sam")]
    assert companions.mentions(None) == []


def test_sync_stores_group_size_and_links_mentions(db, strava):
    strava.add(summary(1, athlete_count=3))
    strava.add(summary(2, day=1, athlete_count=1))
    strava.add(summary(3, day=2, athlete_count=2))
    strava.descriptions[1] = "With [strava://athletes/123/11]Alex Martin and Jo"
    strava.descriptions[3] = "Again [strava://athletes/123/4]Alex"
    sync(db, strava)
    with db.connect() as conn:
        counts = dict(conn.execute(select(activity.c.source_id, activity.c.athlete_count)).all())
        assert counts == {1: 3, 2: 1, 3: 2}
        assert len(names(conn, 1)) == 1 and names(conn, 1) == names(conn, 3)
        assert conn.execute(select(companion.c.strava_athlete_ids)).scalars().all() == [[123]]
        assert names(conn, 2) == []
        assert (
            conn.execute(select(activity.c.description))
            .scalars()
            .all()
            .count("With Alex Martin and Jo")
            == 1
        )


def test_mention_attaches_to_someone_added_by_hand(db, strava):
    strava.add(summary(1))
    sync(db, strava)
    with db.begin() as conn:
        companions.add(conn, ride_id(conn, 1), "  alex   MARTIN ")
    strava.add(summary(2, day=1))
    strava.descriptions[2] = "[strava://athletes/123/11]Alex Martin"
    sync(db, strava)
    with db.connect() as conn:
        people = conn.execute(select(companion.c.name, companion.c.strava_athlete_ids)).all()
        assert people == [("alex MARTIN", [123])]
        assert names(conn, 2) == ["alex MARTIN"]


def test_removed_mention_stays_removed_after_resync(db, strava):
    strava.add(summary(1))
    strava.descriptions[1] = "[strava://athletes/123/3]Sam"
    sync(db, strava)
    with db.begin() as conn:
        sam = conn.execute(select(companion.c.id)).scalar()
        companions.remove(conn, ride_id(conn, 1), sam)
    strava.add(summary(1, name="Renamed"))
    sync(db, strava)
    with db.connect() as conn:
        assert names(conn, 1) == []
        assert [c.name for c in all_companions(conn)] == ["Sam"]
    with db.begin() as conn:
        companions.add(conn, ride_id(conn, 1), "sam")
        assert names(conn, 1) == ["Sam"]


def test_removing_the_last_hand_added_ride_forgets_the_person(conn):
    ride = add(conn, 1)
    jo = companions.add(conn, ride, "Jo")
    companions.remove(conn, ride, jo)
    assert conn.execute(select(companion)).all() == []
    assert companions.add(conn, ride, "   ") is None


def add(conn, source_id: int, athlete_count: int | None = None) -> int:
    return conn.execute(
        activity.insert()
        .values(
            source_id=source_id,
            type="Ride",
            start_at=datetime(2026, 9, 1, tzinfo=UTC),
            start_at_local=datetime(2026, 9, 1),
            athlete_count=athlete_count,
        )
        .returning(activity.c.id)
    ).scalar_one()


def test_rename_merges_rides_and_strava_ids(conn):
    first, second = add(conn, 1), add(conn, 2)
    companions.link_mentions(conn, first, "[strava://athletes/5/8]kim velo")
    kim = companions.add(conn, second, "Kim")
    companions.add(conn, first, "Kim")
    mentioned = conn.execute(select(companion.c.id).where(companion.c.name == "kim velo"))
    kept = companions.rename(conn, mentioned.scalar(), "kim")
    assert kept == kim
    person = conn.execute(select(companion)).one()
    assert (person.name, person.strava_athlete_ids) == ("Kim", [5])
    links = conn.execute(
        select(activity_companion.c.activity_id, activity_companion.c.source).order_by(
            activity_companion.c.activity_id
        )
    ).all()
    assert links == [(first, "manual"), (second, "manual")]
    companions.link_mentions(conn, second, "[strava://athletes/5/3]kim")
    assert conn.execute(select(companion.c.id)).scalars().all() == [kim]
    assert companions.rename(conn, kim, "Kim V.") == kim


def test_group_and_with_filters(conn):
    solo, group, unknown = add(conn, 1, 1), add(conn, 2, 4), add(conn, 3)
    jo = companions.add(conn, group, "Jo")
    companions.add(conn, solo, "Sam")

    def ids(**params):
        state = FilterState.from_items([(k, str(v)) for k, v in params.items()])
        where = build_where(state, Entity.RIDES, activity)
        return set(conn.execute(select(activity.c.id).where(*where)).scalars())

    assert ids(group="solo") == {solo}
    assert ids(group="group") == {group}
    assert ids(**{"with": jo}) == {group}
    assert unknown not in ids(group="solo") | ids(group="group")
    state = FilterState.from_items([("group", "group")])
    assert facet_counts(conn, state, Entity.RIDES, activity, "group") == {
        "solo": 1,
        "group": 1,
        None: 1,
    }
    assert facet_counts(conn, state, Entity.RIDES, activity, "with") == {jo: 1}
    assert FilterState.from_items([("group", "pairs")]).errors


def test_migration_backfills_existing_rides(engine, db):
    url = engine.url.render_as_string(hide_password=False)
    command.downgrade(alembic_config(url), "0008")
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO activity (source_id, name, type, start_at, start_at_local) "
                    "VALUES (1, 'a', 'Ride', now(), now()), (2, 'b', 'Ride', now(), now())"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO raw_strava_activity (source_id, kind, payload, payload_sha256) "
                    "VALUES (1, 'summary', '{\"athlete_count\": 2}', 'x'), "
                    "(2, 'summary', '{\"athlete_count\": 1}', 'y')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO raw_export_activity (source_id, export_name, payload, "
                    "payload_sha256) VALUES (1, 'e', :p, 'z')"
                ),
                {"p": '{"Activity Description": "with [strava://athletes/9/3]Sam"}'},
            )
    finally:
        command.upgrade(alembic_config(url), "head")
    with engine.connect() as conn:
        assert conn.execute(
            select(activity.c.source_id, activity.c.athlete_count).order_by(activity.c.source_id)
        ).all() == [(1, 2), (2, 1)]
        assert names(conn, 1) == ["Sam"]


@pytest.fixture
def client(db, strava, monkeypatch):
    from fastapi.testclient import TestClient

    from velotheque.web import deps
    from velotheque.web.app import app

    def conn():
        with db.connect() as c:
            yield c

    strava.add(summary(1, athlete_count=3))
    strava.descriptions[1] = "With [strava://athletes/123/3]Sam"
    sync(db, strava)
    app.dependency_overrides[deps.get_conn] = conn
    monkeypatch.setattr("velotheque.web.app.get_engine", lambda: db)
    monkeypatch.setattr("velotheque.web.settings.get_settings", lambda: SETTINGS)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_web_ride_page_add_remove_filter_and_rename(client, db):
    with db.connect() as conn:
        ride = ride_id(conn, 1)
        sam = conn.execute(select(companion.c.id)).scalar()
    page = client.get(f"/rides/{ride}").text
    assert "Rode with" in page and "group of 3" in page and f"/?with={sam}" in page

    added = client.post(f"/rides/{ride}/companions", data={"name": "Jo"}).text
    assert 'id="companions"' in added and "Jo" in added and "<html" not in added
    with db.connect() as conn:
        assert names(conn, 1) == ["Jo", "Sam"]
    client.post(f"/rides/{ride}/companions/{sam}/remove")
    with db.connect() as conn:
        assert names(conn, 1) == ["Jo"]
    assert client.post("/rides/999/companions", data={"name": "X"}).status_code == 404

    explore = client.get("/?group=group").text
    assert "Rode with" in explore and "Group rides" in explore
    settings = client.get("/settings").text
    assert 'id="people"' in settings and "mentioned on Strava" in settings
    response = client.post(f"/companions/{sam}", data={"name": "Jo"}, follow_redirects=False)
    assert response.status_code == 303 and "Merged" in response.headers["location"]
    with db.connect() as conn:
        assert [c.name for c in all_companions(conn)] == ["Jo"]
