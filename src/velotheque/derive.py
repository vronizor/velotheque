from sqlalchemy import Connection, text

from velotheque.tables import DERIVED_TABLES

BUFFER_M = 30.0
# Road crossings leave ~60 m pieces of route inside a ride's buffer; they aren't riding it.
MIN_PIECE_M = 100.0

SIMPLIFY = """
INSERT INTO activity_track_simplified (activity_id, level, geometry)
SELECT t.activity_id, l.level,
       ST_Multi(ST_Transform(
           ST_SimplifyPreserveTopology(ST_Transform(t.geometry, 3857), l.tolerance_m), 4326))
FROM activity_track t
CROSS JOIN (VALUES ('overview', 25.0::float8), ('detail', 5.0::float8)) AS l(level, tolerance_m)
"""

# Routes run to thousands of km, so their overview is coarser than rides' (Explore payload).
SIMPLIFY_ROUTES = """
INSERT INTO route_simplified (route_id, level, geometry)
SELECT r.id, l.level,
       ST_Multi(ST_Transform(
           ST_SimplifyPreserveTopology(ST_Transform(r.geometry, 3857), l.tolerance_m), 4326))
FROM route r
CROSS JOIN (VALUES ('overview', 50.0::float8), ('detail', 5.0::float8)) AS l(level, tolerance_m)
WHERE r.geometry IS NOT NULL
"""


def _utm(geometry: str) -> str:
    centroid = f"ST_Centroid({geometry})"
    return (
        f"(CASE WHEN ST_Y({centroid}) >= 0 THEN 32600 ELSE 32700 END"
        f" + LEAST(60, floor((ST_X({centroid}) + 180) / 6)::int + 1))"
    )


def _buffer(geometry: str) -> str:
    """A metric buffer built in the ride's UTM zone, returned in 4326 so it can be
    intersected with the route in the route's own coordinates: projecting the route back and
    forth would add float noise and stop pieces from different rides from merging. Long
    segments are split first so the buffer follows lines that are straight in lon/lat, as the
    route's are."""
    dense = f"ST_Segmentize({geometry}, 0.005)"
    return f"ST_Transform(ST_Buffer(ST_Transform({dense}, {_utm(geometry)}), :buffer_m), 4326)"


# Fractions are over the route's dissolved length (ST_Intersection dissolves a route's
# exactly repeated out-and-back parts, so the denominator must too): riding a road once
# covers both directions of it.
ROUTES_CTE = """
routes AS MATERIALIZED (
  SELECT route_id, geometry, ST_Length(ST_UnaryUnion(geometry)::geography) AS length_m
  FROM route_simplified WHERE level = 'detail' AND {route_filter}
)
"""

OVERLAPS = f"""
INSERT INTO route_ride_overlap (route_id, activity_id, overlap_m, overlap_fraction, covered)
WITH {ROUTES_CTE},
rides AS MATERIALIZED (
  SELECT a.activity_id, {_buffer("a.geometry")} AS buffer
  FROM activity_track_simplified a
  WHERE a.level = 'detail' AND {{ride_filter}}
    AND EXISTS (SELECT 1 FROM routes r WHERE r.geometry && ST_Expand(a.geometry, 0.001))
),
pieces AS (
  SELECT r.route_id, a.activity_id, r.length_m,
         (ST_Dump(ST_Intersection(ST_ClipByBox2D(r.geometry, Box2D(a.buffer)), a.buffer))).geom
           AS piece
  FROM routes r JOIN rides a ON r.geometry && a.buffer
),
lengths AS (
  SELECT route_id, activity_id, length_m, piece, ST_Length(piece::geography) AS m
  FROM pieces WHERE GeometryType(piece) = 'LINESTRING'
)
SELECT route_id, activity_id, sum(m), LEAST(sum(m) / length_m, 1), ST_Multi(ST_Collect(piece))
FROM lengths WHERE m >= :min_piece_m AND length_m > 0
GROUP BY route_id, activity_id, length_m
"""

# Each ride's covered pieces lie on the route line; a hair-thin buffer around them (0.2 m)
# lets the route be cut by their union without unioning the far larger ride buffers.
COVERAGE = f"""
INSERT INTO route_coverage (route_id, length_m, covered_m, coverage_fraction, covered)
WITH {ROUTES_CTE},
areas AS (
  SELECT route_id, ST_Union(ST_Buffer(covered, :seam_deg)) AS area
  FROM route_ride_overlap WHERE route_id IN (SELECT route_id FROM routes)
  GROUP BY route_id
),
pieces AS (
  SELECT r.route_id, (ST_Dump(ST_Intersection(r.geometry, s.area))).geom AS piece
  FROM routes r JOIN areas s USING (route_id)
),
kept AS (
  SELECT route_id, sum(ST_Length(piece::geography)) AS m, ST_Multi(ST_Collect(piece)) AS covered
  FROM pieces
  WHERE GeometryType(piece) = 'LINESTRING' AND ST_Length(piece::geography) >= :min_piece_m
  GROUP BY route_id
)
SELECT r.route_id, r.length_m, coalesce(k.m, 0),
       LEAST(coalesce(k.m, 0) / NULLIF(r.length_m, 0), 1), k.covered
FROM routes r LEFT JOIN kept k USING (route_id)
"""

PARAMS = {"buffer_m": BUFFER_M, "min_piece_m": MIN_PIECE_M, "seam_deg": 2e-6}


def _overlaps(conn: Connection, route_filter: str, ride_filter: str, params: dict) -> None:
    sql = OVERLAPS.format(route_filter=route_filter, ride_filter=ride_filter)
    conn.execute(text(sql), {**PARAMS, **params})


def _coverage(conn: Connection, route_ids: list[int] | None) -> None:
    if route_ids is None:
        conn.execute(text(COVERAGE.format(route_filter="true")), PARAMS)
        return
    if not route_ids:
        return
    conn.execute(text("DELETE FROM route_coverage WHERE route_id = ANY(:ids)"), {"ids": route_ids})
    conn.execute(
        text(COVERAGE.format(route_filter="route_id = ANY(:ids)")), {**PARAMS, "ids": route_ids}
    )


def derive_activities(conn: Connection, activity_ids: list[int]) -> None:
    if not activity_ids:
        return
    ids = {"ids": activity_ids}
    conn.execute(text("DELETE FROM activity_track_simplified WHERE activity_id = ANY(:ids)"), ids)
    conn.execute(text(SIMPLIFY + " WHERE t.activity_id = ANY(:ids)"), ids)
    before = conn.execute(
        text("DELETE FROM route_ride_overlap WHERE activity_id = ANY(:ids) RETURNING route_id"),
        ids,
    ).scalars()
    affected = set(before)
    _overlaps(conn, "true", "a.activity_id = ANY(:ids)", ids)
    affected |= set(
        conn.execute(
            text("SELECT route_id FROM route_ride_overlap WHERE activity_id = ANY(:ids)"), ids
        ).scalars()
    )
    _coverage(conn, sorted(affected))


def derive_routes(conn: Connection, route_ids: list[int]) -> None:
    if not route_ids:
        return
    ids = {"ids": route_ids}
    for table in ("route_simplified", "route_ride_overlap", "route_coverage"):
        conn.execute(text(f"DELETE FROM {table} WHERE route_id = ANY(:ids)"), ids)
    conn.execute(text(SIMPLIFY_ROUTES + " AND r.id = ANY(:ids)"), ids)
    _overlaps(conn, "route_id = ANY(:ids)", "true", ids)
    _coverage(conn, route_ids)


def rebuild_derived(conn: Connection) -> None:
    conn.execute(text(f"TRUNCATE {', '.join(t.name for t in DERIVED_TABLES)}"))
    conn.execute(text(SIMPLIFY))
    conn.execute(text(SIMPLIFY_ROUTES))
    _overlaps(conn, "true", "true", {})
    _coverage(conn, None)
