import sqlite3
import struct

import pytest
from sqlalchemy import select, text

from tests.fake_strava import SETTINGS, summary, sync
from tests.test_routes import add_ride, add_route, east
from velotheque.derive import derive_regions, rebuild_derived
from velotheque.filters import Entity, FilterState, build_where, facet_counts
from velotheque.normalize import MENTION
from velotheque.queries import region_status, ride_regions, search_regions
from velotheque.regions import gpkg_wkb, load, load_country, read_gadm, remove, resolve
from velotheque.tables import activity, activity_region, region, route, route_region


def square(west, south, east_, north) -> bytes:
    ring = [(west, south), (east_, south), (east_, north), (west, north), (west, south)]
    polygon = struct.pack("<BII", 1, 3, 1) + struct.pack("<I", len(ring))
    polygon += b"".join(struct.pack("<dd", x, y) for x, y in ring)
    return struct.pack("<BII", 1, 6, 1) + polygon


def gpkg_blob(wkb: bytes, envelope: bool = False) -> bytes:
    flags = 0b00000011 if envelope else 0b00000001  # little-endian, optional XY envelope
    header = b"GP" + bytes([0, flags]) + struct.pack("<i", 4326)
    return header + (struct.pack("<4d", 0, 0, 0, 0) if envelope else b"") + wkb


# A made-up country: two level-1 regions, each split into level-2 regions, plus a lake.
LEVELS = {
    0: [("XTC", "Testland", None, None, None, None, (6.0, 46.0, 6.2, 46.2))],
    1: [
        ("XTC.1_1", "Genève", "Genf|Ginevra|NA", "Canton", "Canton", "XTC", (6.0, 46.0, 6.1, 46.2)),
        ("XTC.2_1", "València", "Valencia", "Provincia", "Province", "XTC", (6.1, 46.0, 6.2, 46.2)),
    ],
    2: [
        ("XTC.1.1_1", "Lancy", None, "Commune", "Districts", "XTC.1_1", (6.0, 46.0, 6.1, 46.1)),
        ("XTC.1.2_1", "Lac", None, "Water body", "Water body", "XTC.1_1", (6.0, 46.1, 6.1, 46.2)),
        ("XTC.2.1_1", "Onex", None, "Commune", "Municipality", "XTC.2_1", (6.1, 46.0, 6.2, 46.2)),
    ],
}


@pytest.fixture
def gadm(tmp_path):
    path = tmp_path / "gadm41_XTC.gpkg"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT)")
    for level, rows in LEVELS.items():
        table = f"ADM_ADM_{level}"
        db.execute("INSERT INTO gpkg_contents VALUES (?, 'features')", (table,))
        if level == 0:
            db.execute(f"CREATE TABLE {table} (fid INTEGER PRIMARY KEY, geom BLOB, GID_0, COUNTRY)")
            for gid, name, *_, box in rows:
                db.execute(
                    f"INSERT INTO {table} (geom, GID_0, COUNTRY) VALUES (?, ?, ?)",
                    (gpkg_blob(square(*box), envelope=True), gid, name),
                )
            continue
        db.execute(
            f"CREATE TABLE {table} (fid INTEGER PRIMARY KEY, geom BLOB, GID_{level}, NAME_{level},"
            f" VARNAME_{level}, TYPE_{level}, ENGTYPE_{level}, GID_{level - 1})"
        )
        for gid, name, variants, local_type, english_type, parent, box in rows:
            db.execute(
                f"INSERT INTO {table} VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)",
                (
                    gpkg_blob(square(*box)),
                    gid,
                    name,
                    variants or "NA",
                    local_type,
                    english_type,
                    parent,
                ),
            )
    db.commit()
    db.close()
    return path


INSIDE = 1000  # metres north of the country's southern edge


def lancy_ride(conn, source_id=1):
    return add_ride(conn, source_id, east(5, north_m=INSIDE))


def onex_ride(conn, source_id=2):
    return add_ride(conn, source_id, east(15, start_km=10, north_m=INSIDE))


def tags(conn, table, key, entity_id):
    return set(conn.execute(select(table.c.region_id).where(table.c[key] == entity_id)).scalars())


def ride_ids(conn, items):
    state = FilterState.from_items(items)
    return set(
        conn.execute(
            select(activity.c.id).where(*build_where(state, Entity.RIDES, activity))
        ).scalars()
    )


def test_read_gadm(gadm):
    regions = list(read_gadm(gadm))
    assert [r.id for r in regions] == ["XTC", "XTC.1_1", "XTC.2_1", "XTC.1.1_1", "XTC.2.1_1"]
    country, geneve, valencia, lancy, _ = regions
    assert (country.type_label, country.parent_id) == ("Country", None)
    assert geneve.alt_names == ["Genf", "Ginevra"]
    assert valencia.type_label == "Province" and lancy.type_label == "District"
    assert lancy.wkb == square(6.0, 46.0, 6.1, 46.1)
    assert country.wkb == square(6.0, 46.0, 6.2, 46.2)


def test_gpkg_header_is_checked():
    with pytest.raises(ValueError):
        gpkg_wkb(b"XX" + bytes(30))


def test_resolve_wants_three_letter_codes(gadm, tmp_path):
    assert resolve(str(gadm), tmp_path) == gadm
    with pytest.raises(ValueError, match="FRA, not FR"):
        resolve("FR", tmp_path)
    with pytest.raises(ValueError, match="no such file"):
        resolve(str(tmp_path / "missing.gpkg"), tmp_path)


def test_rides_get_their_region_and_its_parents(conn, gadm):
    lancy, onex = lancy_ride(conn), onex_ride(conn)
    far = add_ride(conn, 3, [(40.0, -3.0, None), (40.01, -3.0, None)])
    report = load_country(conn, gadm)
    derive_regions(conn)
    assert (report.country, report.name, report.regions) == ("XTC", "Testland", 5)
    assert tags(conn, activity_region, "activity_id", lancy) == {"XTC", "XTC.1_1", "XTC.1.1_1"}
    assert tags(conn, activity_region, "activity_id", onex) == {"XTC", "XTC.2_1", "XTC.2.1_1"}
    assert tags(conn, activity_region, "activity_id", far) == set()
    key = conn.execute(select(region.c.search_key).where(region.c.id == "XTC.1_1")).scalar()
    assert key == "|geneve|genf|ginevra|"


def test_sync_and_rebuild_keep_tags(conn, gadm):
    load_country(conn, gadm)
    derive_regions(conn)
    ride = lancy_ride(conn)
    assert "XTC.1.1_1" in tags(conn, activity_region, "activity_id", ride)
    rebuild_derived(conn)
    assert "XTC.1.1_1" in tags(conn, activity_region, "activity_id", ride)


def test_routes_are_tagged(conn, gadm):
    load_country(conn, gadm)
    derive_regions(conn)
    across = add_route(conn, east(15, north_m=INSIDE))
    assert tags(conn, route_region, "route_id", across) == {
        "XTC",
        "XTC.1_1",
        "XTC.1.1_1",
        "XTC.2_1",
        "XTC.2.1_1",
    }
    state = FilterState.from_items([("region", "XTC.2.1_1")])
    found = conn.execute(select(route.c.id).where(*build_where(state, Entity.ROUTES, route)))
    assert found.scalars().all() == [across]


def test_region_filter_and_facets(conn, gadm):
    lancy, onex = lancy_ride(conn), onex_ride(conn)
    far = add_ride(conn, 3, [(40.0, -3.0, None), (40.01, -3.0, None)])
    load_country(conn, gadm)
    derive_regions(conn)
    assert ride_ids(conn, [("region", "XTC.1_1")]) == {lancy}
    assert ride_ids(conn, [("region", "XTC.1_1"), ("region", "XTC.2.1_1")]) == {lancy, onex}
    assert ride_ids(conn, [("region", "none")]) == {far}
    assert ride_ids(conn, [("region", "XTC"), ("region", "none")]) == {lancy, onex, far}
    assert ride_ids(conn, [("region", "XTC"), ("dist_min", "1")]) == set()
    assert FilterState.from_items([("region", "x; drop")]).errors

    state = FilterState.from_items([("region", "XTC.1_1")])
    counts = facet_counts(conn, state, Entity.RIDES, activity, "region")
    assert counts["XTC"] == 2 and counts["XTC.2_1"] == 1 and counts["none"] == 1
    assert ride_regions(conn, lancy)[0]["country"].name == "Testland"
    assert [label for label, _ in ride_regions(conn, lancy)[0]["levels"]] == ["Canton", "District"]


def test_reload_and_remove(db, gadm, tmp_path):
    with db.begin() as conn:
        ride = lancy_ride(conn)
    load(db, [str(gadm)], tmp_path, progress=lambda _: None)
    load(db, [str(gadm)], tmp_path, progress=lambda _: None)
    with db.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM region")).scalar() == 5
        assert "XTC.1.1_1" in tags(conn, activity_region, "activity_id", ride)
        status = region_status(conn)
        assert [c.country for c in status["countries"]] == ["XTC"]
        assert status["rides_untagged"] == 0
        assert remove(conn, ["xtc"]) == ["XTC"]
        assert tags(conn, activity_region, "activity_id", ride) == set()
        assert conn.execute(text("SELECT count(*) FROM region_part")).scalar() == 0


def test_search_ignores_accents_and_matches_variants(conn, gadm):
    lancy_ride(conn)
    load_country(conn, gadm)
    derive_regions(conn)
    state = FilterState()

    def names(term):
        return [r["name"] for r in search_regions(conn, state, term, "rides")[0]]

    assert names("valencia") == ["València"]
    assert names("GENF") == ["Genève"]
    assert names("lan") == ["Lancy"]
    assert names("ncy") == []
    assert names("test") == ["Testland"]
    assert names("a(b") == []
    found, counts = search_regions(conn, state, "genev", "both")
    assert found[0]["context"] == "Testland" and counts["rides"]["XTC.1_1"] == 1


def test_mentions_keep_only_the_name():
    text_ = "With [strava://athletes/123/11]Alex Martin. Next [strava://athletes/4/3]sam"
    assert MENTION.sub("", text_) == "With Alex Martin. Next sam"


def test_sync_strips_mentions(db, strava):
    strava.add(summary(1))
    strava.descriptions[1] = "Thanks [strava://athletes/123/3]Sam!"
    sync(db, strava)
    with db.connect() as conn:
        assert conn.execute(select(activity.c.description)).scalar() == "Thanks Sam!"


@pytest.fixture
def client(db, strava, gadm, monkeypatch):
    from fastapi.testclient import TestClient

    from velotheque.web import deps
    from velotheque.web.app import app

    def conn():
        with db.connect() as c:
            yield c

    with db.begin() as c:
        lancy_ride(c)
        load_country(c, gadm)
        derive_regions(c)
    app.dependency_overrides[deps.get_conn] = conn
    monkeypatch.setattr("velotheque.web.app.get_engine", lambda: db)
    monkeypatch.setattr("velotheque.web.settings.get_settings", lambda: SETTINGS)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_web_region_filter_suggest_and_pages(client, db):
    page = client.get("/?region=XTC.1_1")
    assert page.status_code == 200
    assert 'value="XTC.1_1"' in page.text and "Genève" in page.text
    assert "/regions/outlines.geojson?region=XTC.1_1" in page.text

    suggest = client.get("/regions/suggest?term=ginev")
    assert "Genève" in suggest.text and 'name="region"' in suggest.text
    assert "No region matches" in client.get("/regions/suggest?term=zzz").text

    outlines = client.get("/regions/outlines.geojson?region=XTC.1_1").json()
    assert outlines["features"][0]["properties"]["name"] == "Genève"

    with db.connect() as c:
        ride = c.execute(select(activity.c.id)).scalar()
    ride_page = client.get(f"/rides/{ride}")
    assert "Regions" in ride_page.text and 'href="/?region=XTC.1.1_1"' in ride_page.text

    settings = client.get("/settings")
    assert "Testland" in settings.text and "gadm41_XTC.gpkg" in settings.text
