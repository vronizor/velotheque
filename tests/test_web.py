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
    assert 'id="ride-profile"' in page.text and 'data-resize="ride-map"' in page.text
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


def test_backup_indicator_turns_red_after_48h(client, db):
    from datetime import UTC, datetime, timedelta

    from velotheque.backup import record
    from velotheque.tables import backup_status

    assert "backup-stale" in client.get("/backup/indicator").text
    assert "No backup yet" in client.get("/backup/indicator").text

    with db.begin() as conn:
        record(conn, ok=True, details={"dump": "x.dump"})
    fresh = client.get("/backup/indicator").text
    assert "backup-ok" in fresh and "Backup 0 min ago" in fresh

    with db.begin() as conn:
        record(conn, ok=False, error="session expired")
    assert "backup-warn" in client.get("/backup/indicator").text

    with db.begin() as conn:
        conn.execute(
            backup_status.update().values(finished_at=datetime.now(UTC) - timedelta(hours=49))
        )
    stale = client.get("/backup/indicator").text
    assert "backup-stale" in stale and "Backup 2 d ago" in stale
    assert "older than 48 h" in client.get("/settings").text


ROUTE_GPX = (
    b'<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1">'
    b"<metadata><name>Sierra leg</name></metadata><trk><trkseg>"
    b'<trkpt lat="48.85" lon="2.35"><ele>100</ele></trkpt>'
    b'<trkpt lat="48.8745" lon="2.3745"><ele>300</ele></trkpt>'
    b"</trkseg></trk></gpx>"
)


def _upload(client, *files, **form):
    return client.post(
        "/routes/import",
        files=[("files", (name, content, "application/gpx+xml")) for name, content in files],
        data=form,
        follow_redirects=False,
    )


def test_route_import_page_edit_download_delete(client, db):
    response = _upload(client, ("sierra.gpx", ROUTE_GPX), tags="race, Sierra")
    assert response.status_code == 303
    route_url = response.headers["location"]
    page = client.get(route_url)
    assert "Sierra leg" in page.text and 'id="route-map"' in page.text
    assert 'id="ride-profile"' in page.text and "[0.0,100.0,6" in page.text
    assert "Show these 2 rides in Explore" in page.text

    saved = client.post(
        route_url,
        data={"name": "Renamed", "tags": "gravel,  race ", "notes": "nice", "source": " Komoot"},
    )
    assert "Renamed" in saved.text and "Saved" in saved.text
    assert ">Komoot</a>" in saved.text

    file_link = page.text.split(f'href="{route_url}/files/')[1].split('"')[0]
    download = client.get(f"{route_url}/files/{file_link}")
    assert download.content == ROUTE_GPX and "sierra.gpx" in download.headers["content-disposition"]

    again = _upload(client, ("sierra.gpx", ROUTE_GPX))
    assert again.status_code == 200 and "already in route" in again.text

    ride_id = client.get("/explore/rides.geojson").json()["features"][0]["id"]
    ride_page = client.get(f"/rides/{ride_id}").text
    assert "Renamed" in ride_page and 'data-layer="routes"' in ride_page
    on_map = client.get(f"/rides/{ride_id}/routes.geojson").json()["features"]
    assert [f["properties"]["name"] for f in on_map] == ["Renamed"]
    ridden = client.get(f"{route_url}/rides/{ride_id}.geojson").json()
    assert ridden["type"] == "MultiLineString" and 'data-piece="' in client.get(route_url).text and 'data-piece="' in ride_page
    assert client.get(f"{route_url}/rides/987654.geojson").status_code == 404

    assert client.post(f"{route_url}/delete", follow_redirects=False).status_code == 303
    assert client.get(route_url).status_code == 404


def test_merge_needs_a_name(client):
    response = _upload(client, ("a.gpx", ROUTE_GPX), ("b.gpx", ROUTE_GPX + b" "), merge="true")
    assert "Give the merged route a name" in response.text


def test_explore_routes_and_both(client):
    _upload(client, ("sierra.gpx", ROUTE_GPX), tags="race")
    routes = client.get("/?entity=routes&tag=race")
    assert "Sierra leg" in routes.text and "Col du Galibier" not in routes.text
    assert 'name="entity" value="routes"' in routes.text
    both = client.get("/?entity=both")
    assert "Sierra leg" in both.text and "Col du Galibier" in both.text
    collection = client.get("/explore/routes.geojson?tag=race").json()
    assert [f["properties"]["name"] for f in collection["features"]] == ["Sierra leg"]
    assert client.get("/explore/routes.geojson?tag=none").json()["features"] == []
    on_route = client.get(f"/?overlaps_route={collection['features'][0]['id']}:0")
    assert "Sierra leg" in on_route.text and "Ride 2" in on_route.text
    assert client.get("/routes/import").status_code == 200


def test_bulk_edit_sets_source_and_tags(client):
    _upload(client, ("a.gpx", ROUTE_GPX), tags="race, old")
    _upload(client, ("b.gpx", ROUTE_GPX + b" "), source="Strava")
    ids = [f["id"] for f in client.get("/explore/routes.geojson").json()["features"]]
    response = client.post(
        "/routes/bulk",
        data={
            "ids": ids,
            "source": "file",
            "add_tags": "Race, gravel",
            "remove_tags": "OLD",
            "next": "/?entity=routes&tag=race",
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == "/?entity=routes&tag=race"
    page = client.get("/?entity=routes&source=file&tag=gravel")
    assert page.text.count('name="ids"') == 2 and ">old<" not in page.text
    assert (
        client.post(
            "/routes/bulk", data={"ids": ids[:1], "next": "//evil.example"}, follow_redirects=False
        ).headers["location"]
        == "/"
    )
