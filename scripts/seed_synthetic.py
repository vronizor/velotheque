"""Load synthetic rides into a scratch database for UI and performance checks.

    DATABASE_URL=.../velotheque_seed uv run python scripts/seed_synthetic.py 5000

Creates the database if missing, migrates it, and refuses to touch a database
that already holds real Strava rides.
"""

import math
import random
import sys
import time
from datetime import UTC, datetime, timedelta

from alembic import command
from sqlalchemy import create_engine, func, insert, select, text
from sqlalchemy.engine import make_url

from velotheque.cli import alembic_config
from velotheque.config import get_settings
from velotheque.derive import rebuild_derived
from velotheque.geo import multilinestring_wkt
from velotheque.tables import activity

CENTERS = [(48.85, 2.35), (39.47, -0.38), (46.20, 6.15), (45.19, 5.72), (41.39, 2.17)]
TYPES = ["Ride"] * 6 + ["GravelRide"] * 3 + ["MountainBikeRide", "VirtualRide", "EBikeRide"]
BIKES = ["Road bike", "Gravel", "Commuter", "Old MTB"]


def ensure_database(url) -> None:
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


def track(rng: random.Random, n_points: int) -> tuple[list, float]:
    lat, lng = rng.choice(CENTERS)
    lat += rng.uniform(-0.3, 0.3)
    lng += rng.uniform(-0.3, 0.3)
    heading = rng.uniform(0, 2 * math.pi)
    points, dist = [], 0.0
    for _ in range(n_points):
        heading += rng.gauss(0, 0.03)
        if rng.random() < 0.02:
            heading += rng.choice([-1, 1]) * rng.uniform(0.4, 1.6)
        step = rng.uniform(25, 40)
        lat += step * math.cos(heading) / 111_320
        lng += step * math.sin(heading) / (111_320 * math.cos(math.radians(lat)))
        dist += step
        points.append([round(lat, 6), round(lng, 6)])
    return points, dist


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    url = make_url(get_settings().database_url)
    ensure_database(url)
    command.upgrade(alembic_config(url.render_as_string(hide_password=False)), "head")
    engine = create_engine(url)
    rng = random.Random(42)
    started = time.perf_counter()
    with engine.begin() as conn:
        real = conn.execute(
            select(func.count()).select_from(activity).where(activity.c.source == "strava")
        ).scalar()
        if real:
            sys.exit(f"{url.database} holds {real} real Strava rides; use a scratch database")
        conn.execute(text("DELETE FROM activity WHERE source = 'synthetic'"))
        bike_ids = []
        for i, name in enumerate(BIKES):
            stmt = text(
                "INSERT INTO bike (strava_gear_id, name, retired) VALUES (:g, :n, :r) "
                "ON CONFLICT (strava_gear_id) DO UPDATE SET name = EXCLUDED.name RETURNING id"
            )
            bike_ids.append(
                conn.execute(stmt, {"g": f"synthetic-{i}", "n": name, "r": i == 3}).scalar_one()
            )
        start = datetime(2015, 3, 1, 8, tzinfo=UTC)
        for i in range(count):
            kind = rng.choice(TYPES)
            points, dist = track(rng, rng.randint(300, 3500))
            moving = int(dist / rng.uniform(5.5, 8.5))
            when = start + timedelta(hours=rng.uniform(0, 11.5 * 365 * 24))
            activity_id = conn.execute(
                insert(activity)
                .values(
                    source="synthetic",
                    source_id=i + 1,
                    name=f"Synthetic {kind} #{i + 1}",
                    type=kind,
                    start_at=when,
                    start_at_local=(when + timedelta(hours=1)).replace(tzinfo=None),
                    timezone="Europe/Paris",
                    distance_m=dist,
                    moving_time_s=moving,
                    elapsed_time_s=int(moving * 1.15),
                    elevation_gain_m=rng.uniform(0, 25) * dist / 1000,
                    bike_id=rng.choice(bike_ids + [None]),
                    trainer=kind == "VirtualRide",
                )
                .returning(activity.c.id)
            ).scalar_one()
            if kind != "VirtualRide":
                conn.execute(
                    text(
                        "INSERT INTO activity_track (activity_id, geometry, track_source) "
                        "VALUES (:a, ST_GeomFromText(:w, 4326), 'streams')"
                    ),
                    {"a": activity_id, "w": multilinestring_wkt([points])},
                )
        rebuild_derived(conn)
    print(f"seeded {count} rides into {url.database} in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
