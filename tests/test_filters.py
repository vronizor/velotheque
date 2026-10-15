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
