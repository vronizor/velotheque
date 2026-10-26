from datetime import UTC, datetime

import pytest
from sqlalchemy import insert, select

from velotheque.filters import REGISTRY, Entity, FilterState, build_where
from velotheque.tables import activity, bike

RIDES = [
    # source_id, local start, type, bike, km, elevation m, moving hours
    (1, datetime(2026, 3, 1, 9), "Ride", "road", 40, 300, 1.5),
    (2, datetime(2026, 3, 1, 23, 30), "GravelRide", "gravel", 80, 1200, 4.0),
    (3, datetime(2026, 3, 2, 0, 15), "Ride", None, 20, 50, 0.75),
    (4, datetime(2026, 4, 10, 8), "VirtualRide", None, 30, 400, 1.0),
    (5, datetime(2026, 5, 20, 7), "MountainBikeRide", "gravel", 55, 1500, 3.0),
]


@pytest.fixture
def rides(conn):
    bikes = {
        name: conn.execute(
            insert(bike).values(strava_gear_id=f"b-{name}", name=name).returning(bike.c.id)
        ).scalar_one()
        for name in ("road", "gravel")
    }
    for sid, local, kind, bike_name, km, elev, hours in RIDES:
        conn.execute(
            insert(activity).values(
                source_id=sid,
                name=f"ride {sid}",
                type=kind,
                start_at=local.replace(tzinfo=UTC),
                start_at_local=local,
                distance_m=km * 1000,
                elevation_gain_m=elev,
                moving_time_s=int(hours * 3600),
                bike_id=bikes.get(bike_name),
            )
        )
    return conn, bikes


def matching(conn, query: list[tuple[str, str]], exclude=()) -> set[int]:
    state = FilterState.from_items(query)
    where = build_where(state, Entity.RIDES, activity, exclude=exclude)
    return set(conn.execute(select(activity.c.source_id).where(*where)).scalars())


def test_no_filters_match_everything(rides):
    conn, _ = rides
    assert matching(conn, []) == {1, 2, 3, 4, 5}


def test_dates_are_inclusive_local_days(rides):
    conn, _ = rides
    assert matching(conn, [("after", "2026-03-01"), ("before", "2026-03-01")]) == {1, 2}
    assert matching(conn, [("after", "2026-03-02")]) == {3, 4, 5}
    assert matching(conn, [("before", "2026-04-10")]) == {1, 2, 3, 4}


def test_type_is_or_within_field(rides):
    conn, _ = rides
    assert matching(conn, [("type", "Ride")]) == {1, 3}
    assert matching(conn, [("type", "Ride"), ("type", "VirtualRide")]) == {1, 3, 4}


def test_bike_ids_and_no_bike(rides):
    conn, bikes = rides
    gravel = str(bikes["gravel"])
    assert matching(conn, [("bike", gravel)]) == {2, 5}
    assert matching(conn, [("bike", "none")]) == {3, 4}
    assert matching(conn, [("bike", "none"), ("bike", gravel)]) == {2, 3, 4, 5}


def test_ranges_use_display_units(rides):
    conn, _ = rides
    assert matching(conn, [("dist_min", "40")]) == {1, 2, 5}
    assert matching(conn, [("dist_max", "30")]) == {3, 4}
    assert matching(conn, [("elev_min", "1000"), ("elev_max", "1200")]) == {2}
    assert matching(conn, [("dur_min", "1.5"), ("dur_max", "3")]) == {1, 5}


def test_and_across_fields(rides):
    conn, bikes = rides
    query = [("type", "Ride"), ("type", "GravelRide"), ("dist_min", "30")]
    assert matching(conn, query) == {1, 2}
    query += [("bike", str(bikes["road"]))]
    assert matching(conn, query) == {1}


def test_exclude_drops_only_that_field(rides):
    conn, _ = rides
    query = [("type", "Ride"), ("dist_min", "30")]
    assert matching(conn, query, exclude=["type"]) == {1, 2, 4, 5}
    assert matching(conn, query, exclude=["dist_min"]) == {1, 3}


def test_invalid_and_blank_values_are_ignored():
    state = FilterState.from_items(
        [("after", "yesterday"), ("dist_min", "-3"), ("dist_max", ""), ("nope", "1")]
    )
    assert state.values == {}
    assert set(state.errors) == {"after", "dist_min"}


def test_single_value_fields_keep_last_and_multi_dedupes():
    state = FilterState.from_items(
        [("dist_min", "10"), ("dist_min", "20"), ("type", "Ride"), ("type", "Ride")]
    )
    assert state.values == {"dist_min": [20.0], "type": ["Ride"]}


def test_query_string_round_trip():
    items = [("type", "Ride"), ("type", "GravelRide"), ("after", "2026-01-01"), ("bike", "none")]
    state = FilterState.from_items(items)
    again = FilterState.from_items(
        [tuple(p.split("=")) for p in state.to_query().split("&")]  # type: ignore[misc]
    )
    assert again.values == state.values


def test_route_only_fields_skip_rides():
    assert all(Entity.RIDES in f.entities or Entity.ROUTES in f.entities for f in REGISTRY.values())
    state = FilterState.from_items([("type", "Ride")])
    assert build_where(state, Entity.ROUTES, activity) == []


SEARCHABLE = [
    # source_id, name, description, has_photos, track start (lat, lng)
    (11, "Col du Galibier", "Montée par le Lautaret", True, (45.06, 6.41)),
    (12, "València → Xàtiva", None, False, (39.47, -0.38)),
    (13, "Sortie café", "Pause à la boulangerie de Sète", True, (43.40, 3.69)),
    (14, "Évasion gravel", "Chemins blancs", False, None),
]


@pytest.fixture
def searchable(conn):
    from sqlalchemy import text

    for sid, name, description, photos, start in SEARCHABLE:
        activity_id = conn.execute(
            insert(activity)
            .values(
                source_id=sid,
                name=name,
                description=description,
                type="Ride",
                start_at=datetime(2026, 6, 1, tzinfo=UTC),
                start_at_local=datetime(2026, 6, 1),
                has_photos=photos,
            )
            .returning(activity.c.id)
        ).scalar_one()
        if start:
            lat, lng = start
            conn.execute(
                text(
                    "INSERT INTO activity_track (activity_id, geometry, track_source) VALUES "
                    "(:a, ST_GeomFromText(:w, 4326), 'streams')"
                ),
                {
                    "a": activity_id,
                    "w": f"MULTILINESTRING(({lng} {lat}, {lng + 0.05} {lat + 0.05}))",
                },
            )
    return conn


@pytest.mark.parametrize(
    "q, expected",
    [
        ("galibier", {11}),
        ("GALIB", {11}),
        ("lautaret", {11}),
        ("valencia", {12}),
        ("xativa", {12}),
        ("cafe sete", {13}),
        ("evasion", {14}),
        ("galibeir", {11}),
        ("vélo", set()),
    ],
)
def test_text_search_is_accent_insensitive_prefix_and_fuzzy(searchable, q, expected):
    assert matching(searchable, [("q", q)]) == expected


def test_photos_flag(searchable):
    assert matching(searchable, [("photos", "1")]) == {11, 13}
    assert matching(searchable, [("photos", "0")]) == {12, 14}


def test_bbox_and_area_match_tracks(searchable):
    from velotheque.geo import encode_polyline

    assert matching(searchable, [("bbox", "6.0,44.9,6.6,45.2")]) == {11}
    assert matching(searchable, [("bbox", "-1,39,4,44")]) == {12, 13}
    triangle = encode_polyline([(43.0, 3.0), (44.0, 3.5), (43.0, 4.5)])
    assert matching(searchable, [("area", triangle)]) == {13}
    bowtie = encode_polyline([(43.0, 3.0), (44.0, 4.5), (44.0, 3.0), (43.0, 4.5)])
    assert matching(searchable, [("area", bowtie)]) == {13}


def test_bad_geometry_params_are_ignored():
    state = FilterState.from_items(
        [("bbox", "1,2,3"), ("bbox", "10,0,5,1"), ("area", "!!"), ("area", "_p~iF~ps|U")]
    )
    assert state.values == {} and set(state.errors) == {"bbox", "area"}


def test_facet_counts_exclude_their_own_field(rides):
    from velotheque.filters import facet_counts

    conn, bikes = rides
    state = FilterState.from_items([("type", "Ride"), ("dist_min", "30")])
    types = facet_counts(conn, state, Entity.RIDES, activity, "type")
    assert types == {"Ride": 1, "GravelRide": 1, "VirtualRide": 1, "MountainBikeRide": 1}
    by_bike = facet_counts(conn, state, Entity.RIDES, activity, "bike")
    assert by_bike == {bikes["road"]: 1}


def _add(conn, sid, kind, km):
    conn.execute(
        insert(activity).values(
            source_id=sid,
            name=f"{kind} {sid}",
            type=kind,
            start_at=datetime(2026, 6, 1, tzinfo=UTC),
            start_at_local=datetime(2026, 6, 1),
            distance_m=km * 1000,
            moving_time_s=3600,
        )
    )


def test_foot_activities_are_hidden_unless_asked(rides):
    from velotheque.filters import facet_counts

    conn, _ = rides
    _add(conn, 6, "Run", 10)
    _add(conn, 7, "Hike", 15)
    assert matching(conn, []) == {1, 2, 3, 4, 5}
    assert matching(conn, [("bike", "none")]) == {3, 4}
    assert matching(conn, [("foot", "1")]) == {1, 2, 3, 4, 5, 6, 7}
    assert matching(conn, [("foot", "1"), ("type", "Hike")]) == {7}
    assert matching(conn, [("foot", "0")]) == {1, 2, 3, 4, 5}
    assert matching(conn, [("type", "Run")]) == set()

    state = FilterState.from_items([("dist_min", "12")])
    assert facet_counts(conn, state, Entity.RIDES, activity, "foot") == {False: 5, True: 1}
    assert "Hike" not in facet_counts(conn, state, Entity.RIDES, activity, "type")
    assert matching(conn, [("foot", "1")], exclude=["foot"]) == {1, 2, 3, 4, 5, 6, 7}


def test_foot_default_is_not_in_the_query_string():
    state = FilterState.from_items([("type", "Ride")])
    assert state.to_query() == "type=Ride" and not state.values.get("foot")
