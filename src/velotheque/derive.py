from sqlalchemy import Connection, text

from velotheque.tables import DERIVED_TABLES

SIMPLIFY = """
INSERT INTO activity_track_simplified (activity_id, level, geometry)
SELECT t.activity_id, l.level,
       ST_Multi(ST_Transform(
           ST_SimplifyPreserveTopology(ST_Transform(t.geometry, 3857), l.tolerance_m), 4326))
FROM activity_track t
CROSS JOIN (VALUES ('overview', 25.0::float8), ('detail', 5.0::float8)) AS l(level, tolerance_m)
"""


def derive_activities(conn: Connection, activity_ids: list[int]) -> None:
    if not activity_ids:
        return
    conn.execute(
        text("DELETE FROM activity_track_simplified WHERE activity_id = ANY(:ids)"),
        {"ids": activity_ids},
    )
    conn.execute(text(SIMPLIFY + " WHERE t.activity_id = ANY(:ids)"), {"ids": activity_ids})


def rebuild_derived(conn: Connection) -> None:
    conn.execute(text(f"TRUNCATE {', '.join(t.name for t in DERIVED_TABLES)}"))
    conn.execute(text(SIMPLIFY))
