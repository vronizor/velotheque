import gzip
import zipfile
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from velotheque.derive import derive_activities, rebuild_derived
from velotheque.filters import Entity, FilterState, build_where, facet_counts
from velotheque.geo import elevation_gain
from velotheque.routes import clean_source, import_export_routes, import_files, import_library
from velotheque.tables import activity, route, route_coverage, route_file, route_ride_overlap
from velotheque.trackfiles import parse_route_file

LAT, LNG = 46.0, 6.0
M_LAT = 1 / 111_320
M_LNG = 1 / (111_320 * 0.6947)  # cos(46°)


def east(km: float, step_m: float = 100, start_km: float = 0, north_m: float = 0, ele=None):
    n = round((km - start_km) * 1000 / step_m)
    return [
        (LAT + north_m * M_LAT, LNG + (start_km * 1000 + i * step_m) * M_LNG, ele)
        for i in range(n + 1)
    ]


def gpx(*segments, name="Test route", kind=None, rte=False) -> bytes:
    def pts(points, tag):
        return "".join(
            f'<{tag} lat="{lat}" lon="{lng}">'
            + (f"<ele>{e}</ele>" if e is not None else "")
            + f"</{tag}>"
            for lat, lng, e in points
        )

    meta = f"<metadata><name>{name}</name></metadata>" if name else ""
    kind_tag = f"<type>{kind}</type>" if kind else ""
    if rte:
        body = "".join(f"<rte>{kind_tag}{pts(s, 'rtept')}</rte>" for s in segments)
    else:
        body = "".join(
            f"<trk><name>stage {i}</name>{kind_tag}<trkseg>{pts(s, 'trkpt')}</trkseg></trk>"
            for i, s in enumerate(segments)
        )
    return (
        '<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
        f"{meta}{body}</gpx>"
    ).encode()


def add_ride(conn, source_id: int, *segments) -> int:
    ride_id = conn.execute(
        activity.insert()
        .values(
            source_id=source_id,
            name=f"Ride {source_id}",
            type="Ride",
            start_at=datetime(2026, 9, 1, tzinfo=UTC),
            start_at_local=datetime(2026, 9, 1),
        )
        .returning(activity.c.id)
    ).scalar()
    wkt = (
        "MULTILINESTRING("
        + ", ".join("(" + ", ".join(f"{lng} {lat}" for lat, lng, _ in s) + ")" for s in segments)
        + ")"
    )
    conn.execute(
        text(
            "INSERT INTO activity_track (activity_id, geometry, track_source) "
            "VALUES (:id, ST_GeomFromText(:wkt, 4326), 'streams')"
        ),
        {"id": ride_id, "wkt": wkt},
    )
    derive_activities(conn, [ride_id])
    return ride_id


def add_route(conn, *segments, **kwargs) -> int:
    report = import_files(conn, [("route.gpx", gpx(*segments, **kwargs))])
    return report.created[0][0]


def overlap(conn, route_id, ride_id):
    return conn.execute(
        select(route_ride_overlap.c.overlap_fraction).where(
            route_ride_overlap.c.route_id == route_id, route_ride_overlap.c.activity_id == ride_id
        )
    ).scalar()


def coverage(conn, route_id):
    return conn.execute(
        select(route_coverage.c.coverage_fraction).where(route_coverage.c.route_id == route_id)
    ).scalar()


# Parsing


def test_gpx_tracks_stay_separate_pieces_and_rte_is_read():
    data = parse_route_file(gpx(east(1), east(3, start_km=2), name=None), "race.gpx")
    assert len(data.segments) == 2 and data.name is None
    single = parse_route_file(gpx(east(1), name=None), "x.gpx")
    assert single.name == "stage 0"
    rte = parse_route_file(gpx(east(1), rte=True, kind="cycling"), "r.gpx")
    assert rte.name == "Test route" and rte.kind == "cycling" and len(rte.segments) == 1


def test_elevation_gain_ignores_noise_below_threshold():
    assert elevation_gain([100, 104, 99, 103, 100]) == 0
    assert elevation_gain([100, 150, 145, 200, 100]) == 100
    assert elevation_gain([100, 150, 140, 200, 100]) == 110
    assert elevation_gain([None, 100, 112, None, 130]) == 30
    assert elevation_gain([]) == 0


# Import


def test_distance_does_not_bridge_tracks_and_elevation_is_summed(conn):
    climb = east(30, start_km=20, ele=0)
    climb[-1] = (*climb[-1][:2], 50)
    route_id = add_route(conn, east(10, ele=0), climb)
    found = conn.execute(select(route).where(route.c.id == route_id)).one()
    assert found.distance_m == pytest.approx(20_000, rel=0.01)
    assert found.elevation_gain_m == 50


def test_reimport_is_idempotent_and_merge_keeps_file_order(conn):
    files = [(f"stage_{i}.gpx", gpx(east(i + 1, start_km=i), name=f"S{i}")) for i in (10, 2, 1)]
    merged = import_files(conn, files, merge=True, name="Race")
    assert [name for _, name in merged.created] == ["Race"]
    again = import_files(conn, files, merge=True, name="Race")
    assert again.created == [] and len(again.skipped) == 3
    single = import_files(conn, [files[0]])
    assert single.created == [] and "already in route" in single.skipped[0][1]
    order = conn.execute(
        text(
            "SELECT f.filename FROM route_file rf JOIN raw_gpx_file f ON f.id = rf.raw_gpx_file_id "
            "ORDER BY rf.position"
        )
    ).scalars()
    assert list(order) == ["stage_1.gpx", "stage_2.gpx", "stage_10.gpx"]


def test_unreadable_and_foreign_files_are_reported(conn):
    report = import_files(conn, [("notes.pdf", b"%PDF"), ("bad.gpx", b"<gpx><oops")])
    assert report.created == []
    assert [f for f, _ in report.skipped] == ["notes.pdf", "bad.gpx"]
    assert conn.execute(select(route_file)).first() is None


def test_strava_export_routes_skip_running_and_tag_mtb(conn, tmp_path):
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(
            "routes.csv",
            "Route Name,Route Filename\nTrail loop,routes/1.gpx\nJog,routes/2.gpx\n",
        )
        z.writestr("routes/1.gpx", gpx(east(5), name="x", kind="mountain_biking"))
        z.writestr("routes/2.gpx", gpx(east(3), name="y", kind="running"))
    report = import_export_routes(conn, archive)
    assert [name for _, name in report.created] == ["Trail loop"]
    assert report.skipped == [("routes/2.gpx", "running route")]
    found = conn.execute(select(route.c.source, route.c.tags)).one()
    assert found == ("Strava", ["mtb"])


# Overlap and coverage on synthetic geometries


def test_overlap_fraction_is_share_of_route_within_30_m(conn):
    route_id = add_route(conn, east(10))
    half = add_ride(conn, 1, east(5, north_m=15))
    far = add_ride(conn, 2, east(10, north_m=60))
    assert overlap(conn, route_id, half) == pytest.approx(0.5, abs=0.01)
    assert overlap(conn, route_id, far) is None


def test_road_crossings_do_not_count(conn):
    route_id = add_route(conn, east(10))
    crossing = [(LAT + d * M_LAT, LNG + 5000 * M_LNG, None) for d in range(-2000, 2001, 100)]
    ride = add_ride(conn, 1, crossing)
    assert overlap(conn, route_id, ride) is None
    assert coverage(conn, route_id) == 0


def test_out_and_back_route_is_covered_by_one_pass(conn):
    out = east(4)
    route_id = add_route(conn, out + out[-2::-1])
    ride = add_ride(conn, 1, east(4, north_m=10))
    assert overlap(conn, route_id, ride) == pytest.approx(1, abs=0.02)


def test_coverage_merges_overlapping_rides_without_double_counting(conn):
    route_id = add_route(conn, east(10))
    first = add_ride(conn, 1, east(6, north_m=5))
    second = add_ride(conn, 2, east(10, start_km=4, north_m=-5))
    assert overlap(conn, route_id, first) == pytest.approx(0.6, abs=0.01)
    assert overlap(conn, route_id, second) == pytest.approx(0.6, abs=0.01)
    assert coverage(conn, route_id) == pytest.approx(1, abs=0.01)


def test_new_ride_updates_coverage_and_rebuild_matches(conn):
    route_id = add_route(conn, east(10))
    assert coverage(conn, route_id) == 0
    add_ride(conn, 1, east(3))
    assert coverage(conn, route_id) == pytest.approx(0.3, abs=0.01)
    before = conn.execute(text("SELECT * FROM route_ride_overlap ORDER BY 1, 2")).all()
    rebuild_derived(conn)
    after = conn.execute(text("SELECT * FROM route_ride_overlap ORDER BY 1, 2")).all()
    assert [r[:2] for r in after] == [r[:2] for r in before]
    assert coverage(conn, route_id) == pytest.approx(0.3, abs=0.01)


# Filters


def _ids(conn, table, entity, query):
    state = FilterState.from_items(query)
    return sorted(
        conn.execute(select(table.c.id).where(*build_where(state, entity, table))).scalars()
    )


def test_route_filters(conn):
    a = add_route(conn, east(10), name="Alpine")
    b = add_route(conn, east(10, north_m=5000), name="Bocage")
    c = add_route(conn, east(10, north_m=10_000), name="Causses")
    conn.execute(route.update().where(route.c.id == a).values(tags=["race", "gravel"]))
    conn.execute(route.update().where(route.c.id == b).values(tags=["gravel"], source="Strava"))
    ride = add_ride(conn, 1, east(8, north_m=10))

    assert _ids(conn, route, Entity.ROUTES, [("tag", "race"), ("tag", "gravel")]) == [a, b]
    assert _ids(conn, route, Entity.ROUTES, [("tag", "gravel"), ("source", "file")]) == [a]
    assert _ids(conn, route, Entity.ROUTES, [("coverage_min", "50")]) == [a]
    assert _ids(conn, route, Entity.ROUTES, [("coverage_max", "10")]) == [b, c]
    assert _ids(conn, route, Entity.ROUTES, [("q", "causes")]) == [c]
    assert _ids(conn, activity, Entity.RIDES, [("overlaps_route", str(a))]) == [ride]
    assert _ids(conn, activity, Entity.RIDES, [("overlaps_route", f"{a}:90")]) == []
    assert _ids(conn, activity, Entity.RIDES, [("overlaps_route", f"{b}:0")]) == []
    state = FilterState.from_items([("tag", "race"), ("source", "file")])
    assert facet_counts(conn, state, Entity.ROUTES, route, "tag") == {"race": 1, "gravel": 1}
    assert facet_counts(conn, state, Entity.ROUTES, route, "source") == {"file": 1}


def test_ride_only_filters_leave_routes_alone(conn):
    add_route(conn, east(10))
    assert len(_ids(conn, route, Entity.ROUTES, [("type", "Ride"), ("bike", "none")])) == 1
    assert FilterState.from_items([("overlaps_route", "3:150")]).errors


def test_gzipped_gpx_is_accepted(conn):
    report = import_files(conn, [("a.gpx.gz", gzip.compress(gpx(east(2))))])
    assert len(report.created) == 1


def test_source_is_free_text_but_reuses_spellings(conn):
    assert clean_source(conn, "  strava ") == "Strava"
    assert clean_source(conn, "") == "file"
    route_id = add_route(conn, east(2))
    conn.execute(route.update().where(route.c.id == route_id).values(source="Komoot"))
    assert clean_source(conn, "KOMOOT") == "Komoot"
    assert clean_source(conn, "x" * 50) == "x" * 40


def test_library_folder_merges_subfolders(conn, tmp_path):
    (tmp_path / "loop.gpx").write_bytes(gpx(east(3), name="Loop"))
    (tmp_path / "loop.geojson").write_text("{}")
    race = tmp_path / "2023_some-race"
    race.mkdir()
    for i in (2, 1):
        (race / f"stage{i}.gpx").write_bytes(gpx(east(i + 1, start_km=i), name=f"S{i}"))
    report = import_library(conn, tmp_path, tags=["x"])
    assert sorted(name for _, name in report.created) == ["2023_some-race", "Loop"]
    assert report.skipped == []
    assert import_library(conn, tmp_path).created == []
