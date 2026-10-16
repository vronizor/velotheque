import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePath

import polars as pl
from sqlalchemy import Connection, select, text
from sqlalchemy.dialects.postgresql import insert

from velotheque.derive import derive_routes
from velotheque.geo import elevation_gain
from velotheque.tables import ROUTE_SOURCE_MAX, ROUTE_SOURCES, raw_gpx_file, route, route_file
from velotheque.trackfiles import RouteData, parse_route_file

ROUTE_SUFFIXES = (".gpx", ".tcx", ".fit")
SKIPPED_STRAVA_TYPES = {"running", "walking", "hiking", "trail_running"}
STRAVA_TYPE_TAGS = {"mountain_biking": "mtb", "gravel_cycling": "gravel"}


@dataclass
class ImportReport:
    created: list[tuple[int, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def is_route_file(filename: str) -> bool:
    name = filename.lower().removesuffix(".gz")
    return name.endswith(ROUTE_SUFFIXES)


def natural_key(name: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def clean_tags(tags) -> list[str]:
    out: list[str] = []
    for tag in tags:
        tag = " ".join(tag.split())[:40]
        if tag and tag.lower() not in (t.lower() for t in out):
            out.append(tag)
    return out


def clean_source(conn: Connection, value: str | None) -> str:
    """Free text, but reuses an existing or built-in spelling so "strava" and "Strava" don't
    become two sources."""
    value = " ".join((value or "").split())[:ROUTE_SOURCE_MAX] or "file"
    known = [*conn.execute(select(route.c.source).distinct()).scalars(), *ROUTE_SOURCES]
    return next((s for s in known if s.lower() == value.lower()), value)


def store_file(conn: Connection, filename: str, content: bytes) -> int:
    digest = hashlib.sha256(content).digest()
    conn.execute(
        insert(raw_gpx_file)
        .values(sha256=digest, filename=PurePath(filename).name, content=content)
        .on_conflict_do_nothing(index_elements=["sha256"])
    )
    return conn.execute(select(raw_gpx_file.c.id).where(raw_gpx_file.c.sha256 == digest)).scalar()


def _segments_wkt(segments) -> str:
    return (
        "MULTILINESTRING("
        + ", ".join("(" + ", ".join(f"{lng} {lat}" for lat, lng, _ in s) + ")" for s in segments)
        + ")"
    )


def _parsed(conn: Connection, route_id: int) -> list[RouteData]:
    rows = conn.execute(
        select(raw_gpx_file.c.filename, raw_gpx_file.c.content)
        .join(route_file, route_file.c.raw_gpx_file_id == raw_gpx_file.c.id)
        .where(route_file.c.route_id == route_id)
        .order_by(route_file.c.position)
    ).all()
    return [parse_route_file(r.content, r.filename) for r in rows]


def normalize_route(conn: Connection, route_id: int) -> None:
    """Geometry, distance and elevation come only from the route's raw files."""
    segments = [s for data in _parsed(conn, route_id) for s in data.segments]
    if not segments:
        raise ValueError("no track points")
    altitudes = [[p[2] for p in s] for s in segments]
    has_elevation = any(a is not None for s in altitudes for a in s)
    conn.execute(
        text(
            """
            UPDATE route SET geometry = g, distance_m = ST_Length(g::geography),
                             elevation_gain_m = :gain, altitude_m = CAST(:altitudes AS real[])
            FROM (SELECT ST_GeomFromText(:wkt, 4326) AS g) AS s
            WHERE id = :id
            """
        ),
        {
            "wkt": _segments_wkt(segments),
            "gain": sum(elevation_gain(s) for s in altitudes) if has_elevation else None,
            "altitudes": [a for s in altitudes for a in s] if has_elevation else None,
            "id": route_id,
        },
    )


def normalize_routes(conn: Connection) -> list[int]:
    ids = conn.execute(select(route.c.id).order_by(route.c.id)).scalars().all()
    for route_id in ids:
        normalize_route(conn, route_id)
    return ids


def _default_name(data: RouteData, filename: str) -> str:
    return data.name or PurePath(filename).name.split(".")[0]


def create_route(
    conn: Connection,
    files: list[tuple[str, bytes]],
    *,
    name: str | None = None,
    source: str = "file",
    tags=(),
    notes: str | None = None,
    report: ImportReport,
) -> int | None:
    """One route from one file, or from several stage files kept in natural filename order.
    A file already used by a route is skipped, so re-importing never duplicates."""
    source = clean_source(conn, source)
    files = sorted(files, key=lambda f: natural_key(PurePath(f[0]).name))
    kept, datas = [], []
    for filename, content in files:
        try:
            data = parse_route_file(content, filename)
        except Exception as error:
            report.skipped.append((filename, f"unreadable: {error}"))
            continue
        if not data.segments:
            report.skipped.append((filename, "no track points"))
            continue
        file_id = store_file(conn, filename, content)
        used = conn.execute(
            select(route.c.id, route.c.name)
            .join(route_file, route_file.c.route_id == route.c.id)
            .where(route_file.c.raw_gpx_file_id == file_id)
        ).first()
        if used:
            report.skipped.append((filename, f"already in route #{used.id} “{used.name}”"))
            continue
        kept.append((filename, file_id))
        datas.append(data)
    if not kept:
        return None
    auto_tags = [STRAVA_TYPE_TAGS[d.kind] for d in datas if d.kind in STRAVA_TYPE_TAGS]
    route_id = conn.execute(
        route.insert()
        .values(
            name=(name or "").strip()[:200] or _default_name(datas[0], kept[0][0]),
            source=source,
            notes=notes or None,
            tags=clean_tags([*tags, *auto_tags]),
        )
        .returning(route.c.id)
    ).scalar()
    conn.execute(
        route_file.insert(),
        [
            {"route_id": route_id, "position": i, "raw_gpx_file_id": file_id}
            for i, (_, file_id) in enumerate(kept)
        ],
    )
    normalize_route(conn, route_id)
    derive_routes(conn, [route_id])
    route_name = conn.execute(select(route.c.name).where(route.c.id == route_id)).scalar()
    report.created.append((route_id, route_name))
    return route_id


def import_files(
    conn: Connection,
    files: list[tuple[str, bytes]],
    *,
    merge: bool = False,
    name: str | None = None,
    source: str = "file",
    tags=(),
) -> ImportReport:
    report = ImportReport()
    accepted = []
    for filename, content in files:
        if is_route_file(filename):
            accepted.append((filename, content))
        else:
            report.skipped.append((filename, "not a GPX, TCX or FIT file"))
    groups = [accepted] if merge else [[f] for f in accepted]
    for group in groups:
        if group:
            create_route(
                conn,
                group,
                name=name if merge or len(accepted) == 1 else None,
                source=source,
                tags=tags,
                report=report,
            )
    return report


def import_library(
    conn: Connection, folder: Path, *, separate=(), source: str = "file", tags=()
) -> ImportReport:
    """A folder as kept on disk: each route file at its top becomes a route, each subfolder
    (a race's stage files) one merged route named after the folder, except the subfolders
    named in `separate`, whose files are routes of their own. Other files, such as GeoJSON
    copies, are ignored."""
    report = ImportReport()
    groups: list[tuple[list[Path], str | None]] = []
    for path in sorted(folder.iterdir(), key=lambda p: natural_key(p.name)):
        if path.is_dir():
            files = [p for p in path.rglob("*") if p.is_file() and is_route_file(p.name)]
            if path.name in separate:
                groups += [([p], None) for p in sorted(files, key=lambda p: natural_key(p.name))]
            elif files:
                groups.append((files, path.name))
        elif is_route_file(path.name):
            groups.append(([path], None))
    for files, name in groups:
        group = [(p.name, p.read_bytes()) for p in files]
        create_route(conn, group, name=name, source=source, tags=tags, report=report)
    return report


def import_export_routes(conn: Connection, zip_path: Path, tags=()) -> ImportReport:
    """The bulk export's routes/*.gpx (routes drawn on Strava); running routes are skipped."""
    report = ImportReport()
    with zipfile.ZipFile(zip_path) as archive:
        names = dict(
            pl.read_csv(archive.read("routes.csv"), infer_schema=False)
            .select("Route Filename", "Route Name")
            .iter_rows()
        )
        for member, route_name in sorted(names.items(), key=lambda kv: natural_key(kv[0])):
            content = archive.read(member)
            kind = parse_route_file(content, member).kind
            if kind in SKIPPED_STRAVA_TYPES:
                report.skipped.append((member, f"{kind} route"))
                continue
            create_route(
                conn,
                [(member, content)],
                name=route_name,
                source="Strava",
                tags=tags,
                report=report,
            )
    return report
