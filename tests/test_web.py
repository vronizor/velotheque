import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.fake_strava import SETTINGS, summary, sync
from velotheque.tables import activity
from velotheque.web import deps
from velotheque.web.app import app


@pytest.fixture
def client(db, strava, monkeypatch):
    def conn():
        with db.connect() as c:
            yield c

    app.dependency_overrides[deps.get_conn] = conn
    monkeypatch.setattr("velotheque.web.app.get_engine", lambda: db)
    monkeypatch.setattr("velotheque.web.settings.get_settings", lambda: SETTINGS)
    strava.add(summary(1, name="Col du Galibier"))
    strava.add(summary(2, day=3, sport_type="GravelRide", distance=90000.0))
    sync(db, strava)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_explore_full_page_and_partial(client):
    page = client.get("/?type=GravelRide")
    assert page.status_code == 200
    assert 'id="filters"' in page.text and "Col du Galibier" not in page.text

    partial = client.get(
        "/?type=Ride&type=GravelRide&sort=distance",
        headers={"HX-Request": "true", "HX-Target": "results"},
    )
    assert 'id="filters"' not in partial.text
    assert partial.text.index("Col du Galibier") < partial.text.index("Ride 2")


def test_geojson_follows_filters(client):
    everything = client.get("/explore/rides.geojson").json()
    gravel = client.get("/explore/rides.geojson?dist_min=60").json()
    assert len(everything["features"]) == 2
    assert [f["properties"]["name"] for f in gravel["features"]] == ["Ride 2"]
    assert everything["features"][0]["geometry"]["type"] == "MultiLineString"


def test_ride_page(client, db):
    with db.connect() as conn:
        ride_id = conn.execute(select(activity.c.id).where(activity.c.source_id == 1)).scalar()
    page = client.get(f"/rides/{ride_id}")
    assert page.status_code == 200
    assert "Col du Galibier" in page.text and 'id="ride-map"' in page.text
    assert client.get("/rides/987654").status_code == 404


def test_settings_and_callback_state_check(client):
    page = client.get("/settings")
    assert "Connected as athlete" in page.text
    response = client.get("/strava/callback?code=x&state=forged&scope=read", follow_redirects=False)
    assert response.status_code == 303
    assert "state+mismatch" in response.headers["location"]


def test_connect_sets_state_cookie(client):
    response = client.get("/strava/connect", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert "scope=read%2Cactivity%3Aread_all%2Cprofile%3Aread_all" in location
    assert "strava_oauth_state" in response.cookies


def test_sync_widget_reports_latest_job(client):
    widget = client.get("/jobs/latest").text
    assert "Sync now" in widget and "Synced" in widget
    assert json.loads(client.get("/explore/rides.geojson").text)["type"] == "FeatureCollection"
