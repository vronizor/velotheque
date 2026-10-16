from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select

from velotheque import components
from velotheque.components import ComponentError
from velotheque.filters import Entity, FilterState, build_where
from velotheque.tables import activity, bike, component_assignment

T0 = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)


def day(n: float) -> datetime:
    return T0 + timedelta(days=n)


@pytest.fixture
def bikes(conn):
    ids = conn.execute(
        insert(bike).returning(bike.c.id),
        [
            {"strava_gear_id": "b1", "name": "Road"},
            {"strava_gear_id": "b2", "name": "Trainer bike"},
        ],
    ).scalars()
    return list(ids)


_next_ride = iter(range(1, 10_000))


def ride(conn, bike_id, start, km=50.0, hours=2.0, kind="Ride", trainer=False) -> int:
    return conn.execute(
        insert(activity)
        .values(
            source_id=next(_next_ride),
            name="r",
            type=kind,
            start_at=start,
            start_at_local=start.replace(tzinfo=None),
            distance_m=km * 1000,
            moving_time_s=int(hours * 3600),
            bike_id=bike_id,
            trainer=trainer,
        )
        .returning(activity.c.id)
    ).scalar_one()


def used(conn, component_id, name=None, now=None):
    rows = components.usage(conn, [component_id], now=now)
    return {u.name: u for u in rows}[name] if name else rows[0]


def test_ride_at_mount_counts_and_removal_is_exclusive(conn, bikes):
    road, _ = bikes
    chain = components.create(conn, "Chain")
    components.add_service_type(conn, chain, "Wax", 1, 300, "km")
    components.mount(conn, chain, road, day(0))
    ride(conn, road, day(-1))
    ride(conn, road, day(0))
    ride(conn, road, day(5))
    components.unmount(conn, chain, day(10))
    ride(conn, road, day(10))
    u = used(conn, chain)
    assert (u.ride_count, u.used) == (2, 100.0)
    assert u.fraction == pytest.approx(1 / 3) and u.status == "ok"


def test_swap_across_bikes_follows_the_component(conn, bikes):
    road, trainer_bike = bikes
    wheels = components.create(conn, "Wheels")
    components.add_service_type(conn, wheels, "Bearings", 1, 10, "hours")
    components.mount(conn, wheels, road, day(0))
    ride(conn, road, day(1), hours=3)
    ride(conn, trainer_bike, day(2), hours=7)  # not on the trainer bike yet
    components.mount(conn, wheels, trainer_bike, day(3))
    ride(conn, road, day(4), hours=11)  # left Road
    ride(conn, trainer_bike, day(4), hours=4)
    u = used(conn, wheels)
    assert u.used == pytest.approx(7.0) and u.ride_count == 2
    periods = conn.execute(
        select(component_assignment.c.bike_id, component_assignment.c.removed_at).order_by("id")
    ).all()
    assert periods == [(road, day(3)), (trainer_bike, None)]


def test_reset_levels(conn, bikes):
    road, _ = bikes
    fork = components.create(conn, "Fork")
    lowers = components.add_service_type(conn, fork, "Lowers", 1, 50, "hours")
    full = components.add_service_type(conn, fork, "Full", 2, 200, "hours")
    components.mount(conn, fork, road, day(0))
    ride(conn, road, day(1), hours=10)
    components.log_event(conn, fork, lowers, day(2))
    ride(conn, road, day(3), hours=5)
    assert used(conn, fork, "Lowers").used == 5 and used(conn, fork, "Full").used == 15

    components.log_event(conn, fork, full, day(4))
    ride(conn, road, day(5), hours=1)
    assert used(conn, fork, "Lowers").used == 1 and used(conn, fork, "Full").used == 1

    components.log_event(conn, fork, None, day(6), notes="Squeaky, cleaned seals")
    assert used(conn, fork, "Lowers").used == 1


def test_ride_at_reset_time_is_before_the_reset(conn, bikes):
    road, _ = bikes
    chain = components.create(conn, "Chain")
    wax = components.add_service_type(conn, chain, "Wax", 1, 300, "km")
    components.mount(conn, chain, road, day(0))
    ride(conn, road, day(1))
    components.log_event(conn, chain, wax, day(1))
    assert used(conn, chain).used == 0


def test_days_count_calendar_time(conn, bikes):
    road, _ = bikes
    tyre = components.create(conn, "Tyre")
    sealant = components.add_service_type(conn, tyre, "Sealant", 1, 90, "days")
    assert used(conn, tyre, now=day(10)).used is None
    components.mount(conn, tyre, road, day(0))
    assert used(conn, tyre, now=day(81)).used == pytest.approx(81)
    assert used(conn, tyre, now=day(81)).status == "soon"
    components.log_event(conn, tyre, sealant, day(50))
    assert used(conn, tyre, now=day(150)).used == pytest.approx(100)
    assert used(conn, tyre, now=day(150)).status == "over"


def test_or_days_whichever_comes_first(conn, bikes):
    road, _ = bikes
    fork = components.create(conn, "Fork")
    components.add_service_type(conn, fork, "Lowers", 1, 50, "hours")
    full = components.add_service_type(conn, fork, "Full", 2, 200, "hours", or_days="365")
    components.mount(conn, fork, road, day(0))
    ride(conn, road, day(10), hours=15)
    quiet = used(conn, fork, "Full", now=day(330))
    assert quiet.by_days and quiet.status == "soon" and quiet.fraction == pytest.approx(330 / 365)
    assert used(conn, fork, "Full", now=day(366)).status == "over"
    assert used(conn, fork, "Lowers", now=day(366)).status == "ok"

    components.log_event(conn, fork, full, day(370))
    ride(conn, road, day(380), hours=190)
    busy = used(conn, fork, "Full", now=day(400))
    assert not busy.by_days and busy.used == 190 and busy.status == "soon"
    assert busy.days == pytest.approx(30)
    assert used(conn, fork, "Lowers", now=day(400)).used == 190


def test_or_days_validation(conn, bikes):
    tyre = components.create(conn, "Tyre")
    sealant = components.add_service_type(conn, tyre, "Sealant", 1, 90, "days", or_days=30)
    assert used(conn, tyre).or_days is None  # a days interval needs no second clock
    with pytest.raises(ComponentError, match="above 0"):
        components.add_service_type(conn, tyre, "Wear", 1, 3000, "km", or_days="-1")
    components.edit_service_type(conn, tyre, sealant, "Sealant", 1, 3000, "km", "all", "")
    assert used(conn, tyre).or_days is None
    components.edit_service_type(
        conn, tyre, sealant, "Sealant", 1, 3000, "km", "all", "180", "  Top up 30 ml\n"
    )
    assert (used(conn, tyre).or_days, used(conn, tyre).description) == (180, "Top up 30 ml")
    new = components.create(conn, "New tyre")
    components.copy_schedule(conn, tyre, new)
    assert (used(conn, new).or_days, used(conn, new).description) == (180, "Top up 30 ml")


def test_indoor_scopes(conn, bikes):
    _, trainer_bike = bikes
    chain = components.create(conn, "Chain")
    cassette = components.create(conn, "Bike cassette")
    trainer = components.create(conn, "Trainer cassette")
    for c, scope in ((chain, "all"), (cassette, "outdoor"), (trainer, "indoor")):
        components.add_service_type(conn, c, "Check", 1, 1000, "km", rides=scope)
        components.mount(conn, c, trainer_bike, day(0))
    ride(conn, trainer_bike, day(1), km=40)
    ride(conn, trainer_bike, day(2), km=20, kind="VirtualRide")
    ride(conn, trainer_bike, day(3), km=30, trainer=True)
    assert used(conn, chain).used == 90
    assert used(conn, cassette).used == 40
    assert used(conn, trainer).used == 50
    assert components.lifetime(conn)[cassette].rides == 3


def test_one_bike_at_a_time(conn, bikes):
    road, trainer_bike = bikes
    chain = components.create(conn, "Chain")
    components.add_assignment(conn, chain, road, day(0), day(10))
    components.add_assignment(conn, chain, trainer_bike, day(10), day(20))  # adjacent is fine
    with pytest.raises(ComponentError, match="already on a bike"):
        components.add_assignment(conn, chain, trainer_bike, day(5), day(12))
    with pytest.raises(ComponentError, match="after mounting"):
        components.add_assignment(conn, chain, trainer_bike, day(30), day(25))
    components.mount(conn, chain, road, day(20))
    with pytest.raises(ComponentError, match="already on that bike"):
        components.mount(conn, chain, road, day(21))
    with pytest.raises(ComponentError, match="after it was mounted"):
        components.mount(conn, chain, trainer_bike, day(19))
    other = components.create(conn, "Other chain")
    components.add_assignment(conn, other, road, day(0), None)  # two chains on a bike: allowed


def test_retire_unmounts_and_copy_schedule(conn, bikes):
    road, _ = bikes
    old = components.create(conn, "Old chain", kind="Chain")
    components.add_service_type(conn, old, "Wax", 1, 300, "km")
    components.add_service_type(conn, old, "Check wear", 1, 1000, "km", rides="outdoor")
    components.mount(conn, old, road, day(0))
    components.retire(conn, old, day(30))
    assert components.current_assignment(conn, old) is None

    new = components.create(conn, "New chain", kind="Chain")
    components.add_service_type(conn, new, "wax", 1, 250, "km")
    assert components.copy_schedule(conn, old, new) == 1
    names = {u.name: u.rides for u in components.usage(conn, [new])}
    assert names == {"wax": "all", "Check wear": "outdoor"}


def test_service_type_with_events_cannot_be_deleted(conn, bikes):
    chain = components.create(conn, "Chain")
    wax = components.add_service_type(conn, chain, "Wax", 1, 300, "km")
    components.log_event(conn, chain, wax, day(1))
    with pytest.raises(ComponentError, match="in use"):
        components.delete_service_type(conn, chain, wax)
    other = components.create(conn, "Other")
    with pytest.raises(ComponentError):
        components.log_event(conn, other, wax, day(1))  # a service of another component


def test_component_filter(conn, bikes):
    road, trainer_bike = bikes
    chain = components.create(conn, "Chain")
    components.add_assignment(conn, chain, road, day(0), day(10))
    components.add_assignment(conn, chain, trainer_bike, day(10), None)
    expected = {
        ride(conn, road, day(0)),
        ride(conn, trainer_bike, day(12)),
    }
    ride(conn, road, day(-1))
    ride(conn, road, day(10))
    ride(conn, None, day(5))
    state = FilterState.from_items([("component", str(chain))])
    found = conn.execute(
        select(activity.c.id).where(*build_where(state, Entity.RIDES, activity))
    ).scalars()
    assert set(found) == expected


def test_mounted_on_ride(conn, bikes):
    road, _ = bikes
    chain = components.create(conn, "Chain", kind="Chain")
    components.add_assignment(conn, chain, road, day(0), day(10))
    inside, after = ride(conn, road, day(9)), ride(conn, road, day(10))
    assert [c.name for c in components.mounted_on_ride(conn, inside)] == ["Chain"]
    assert components.mounted_on_ride(conn, after) == []


def test_parse_local():
    zone = components.ZoneInfo("Europe/Zurich")
    assert components.parse_local("2026-07-01T10:00", zone) == datetime(2026, 7, 1, 8, tzinfo=UTC)
    with pytest.raises(ComponentError):
        components.parse_local("yesterday", zone)
