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


def test_partial_carries_facets_out_of_band(client):
    partial = client.get(
        "/?type=GravelRide", headers={"HX-Request": "true", "HX-Target": "results"}
    ).text
    assert 'id="facets" hx-swap-oob="true"' in partial
    assert 'id="area-filter" class="area-filter" hx-swap-oob="true"' in partial
    facets = partial[partial.index('id="facets"') :]
    assert 'value="Ride" form="filters" > Ride <span class="count">1</span>' in facets
    assert 'value="GravelRide" form="filters" checked> GravelRide <span class="count">1' in facets


def test_search_filters_results_and_map(client):
    page = client.get("/?q=galibier").text
    assert "Col du Galibier" in page and "Ride 2" not in page
    assert len(client.get("/explore/rides.geojson?q=GALIB").json()["features"]) == 1


def test_saved_views_round_trip(client):
    saved = client.post("/views", data={"name": "Gravel", "query_string": "?type=GravelRide"})
    assert "Saved “Gravel”" in saved.text and 'href="/?type=GravelRide"' in saved.text
    client.post("/views", data={"name": "Gravel", "query_string": "?type=Ride"})
    page = client.get("/").text
    assert page.count('href="/?type=Ride">Gravel</a>') == 1
    view_id = page.split('hx-delete="/views/')[1].split('"')[0]
    assert "Gravel" not in client.delete(f"/views/{view_id}").text.split("</ul>")[0]


def test_photos_are_served_and_shown(client, db, tmp_path, monkeypatch):
    from tests.fake_strava import jpeg_bytes
    from velotheque.photos import materialize_photos

    settings = SETTINGS.model_copy(update={"data_dir": tmp_path})
    photo = tmp_path / "photos" / "1" / "abc.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(jpeg_bytes())
    with db.begin() as conn:
        ride_id = conn.execute(select(activity.c.id).where(activity.c.source_id == 1)).scalar()
        conn.exec_driver_sql(
            "INSERT INTO raw_export_activity (source_id, export_name, payload, payload_sha256) "
            "VALUES (1, 'e', '{\"Media\": \"||media/abc.jpg\"}', '\\x00')"
        )
        assert materialize_photos(conn, ride_id, 1, settings) == 1
    page = client.get(f"/rides/{ride_id}").text
    assert 'src="/photos/1/thumbs/abc.jpg"' in page and 'id="lightbox"' in page

    from starlette.staticfiles import StaticFiles

    from velotheque.web.app import app

    mount = next(r for r in app.routes if getattr(r, "name", "") == "photos")
    monkeypatch.setattr(mount, "app", StaticFiles(directory=tmp_path / "photos"))
    response = client.get("/photos/1/abc.jpg")
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"


def test_settings_lists_export_status(client):
    assert "No export imported yet" in client.get("/settings").text
