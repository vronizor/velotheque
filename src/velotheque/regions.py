"""GADM boundaries: read a country's GeoPackage (stdlib sqlite3, no GDAL), load it into
`region`, and tag rides and routes (derive.derive_regions)."""

import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy import Connection, delete, select, text

from velotheque.derive import derive_regions
from velotheque.tables import region_country

GADM_VERSION = "4.1"
GADM_URL = "https://geodata.ucdavis.edu/gadm/gadm4.1/gpkg/gadm41_{code}.gpkg"
_LAYER = re.compile(r"ADM_ADM_(\d)")
# GeoPackage geometry header: magic, version, flags, srs_id, then an envelope whose size
# depends on bits 1-3 of the flags; standard WKB follows.
_ENVELOPE_BYTES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
_TYPE_FIXES = {"Districts": "District"}


@dataclass(frozen=True)
class GadmRegion:
    id: str
    level: int
    name: str
    alt_names: list[str]
    type_label: str
    parent_id: str | None
    wkb: bytes


@dataclass
class LoadReport:
    country: str
    name: str
    regions: int = 0
    levels: dict[int, int] = field(default_factory=dict)


def gpkg_wkb(blob: bytes) -> bytes:
    if blob[:2] != b"GP":
        raise ValueError("not a GeoPackage geometry")
    return blob[8 + _ENVELOPE_BYTES[(blob[3] >> 1) & 7] :]


def _names(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split("|") if v.strip() and v.strip() != "NA"]


def _is_water(*types: str | None) -> bool:
    return any((t or "").lower() == "water body" for t in types)


def read_gadm(path: Path) -> Iterator[GadmRegion]:
    """Every region of a GADM country GeoPackage, country first. Lakes ("Water body") are
    left out."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        layers = sorted(
            (int(m.group(1)), name)
            for (name,) in db.execute("SELECT table_name FROM gpkg_contents")
            if (m := _LAYER.fullmatch(name))
        )
        if not layers or layers[0][0] != 0:
            raise ValueError(f"{path.name} is not a GADM country GeoPackage")
        for level, layer in layers:
            columns = {row[1] for row in db.execute(f'PRAGMA table_info("{layer}")')}
            if level == 0:
                query = f'SELECT GID_0, COUNTRY, NULL, NULL, NULL, NULL, geom FROM "{layer}"'
            else:
                variant = f"VARNAME_{level}" if f"VARNAME_{level}" in columns else "NULL"
                query = (
                    f"SELECT GID_{level}, NAME_{level}, {variant}, TYPE_{level}, "
                    f'ENGTYPE_{level}, GID_{level - 1}, geom FROM "{layer}"'
                )
            for gid, name, variants, local_type, english_type, parent, geom in db.execute(query):
                if geom is None or _is_water(local_type, english_type):
                    continue
                label = english_type or local_type or "Region"
                yield GadmRegion(
                    id=gid,
                    level=level,
                    name=name,
                    alt_names=[v for v in _names(variants) if v != name],
                    type_label="Country" if level == 0 else _TYPE_FIXES.get(label, label),
                    parent_id=parent,
                    wkb=gpkg_wkb(geom),
                )
    finally:
        db.close()


def gadm_file(code: str, cache: Path, progress: Callable[[str], None] = print) -> Path:
    """The country's GeoPackage, downloaded once into the cache folder."""
    path = cache / f"gadm41_{code}.gpkg"
    if path.exists():
        return path
    cache.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    url = GADM_URL.format(code=code)
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as response:
        if response.status_code == 404:
            raise ValueError(f"GADM {GADM_VERSION} has no file for {code}")
        response.raise_for_status()
        size = int(response.headers.get("content-length") or 0)
        progress(f"downloading {url}" + (f" ({size / 1e6:.0f} MB)" if size else ""))
        with partial.open("wb") as out:
            for chunk in response.iter_bytes(1 << 20):
                out.write(chunk)
    partial.replace(path)
    return path


def resolve(argument: str, cache: Path, progress: Callable[[str], None] = print) -> Path:
    """A country code (FRA) or a path to a GADM .gpkg file."""
    if argument.lower().endswith(".gpkg"):
        path = Path(argument)
        if not path.is_file():
            raise ValueError(f"{argument}: no such file")
        return path
    if re.fullmatch(r"[A-Za-z]{3}", argument):
        return gadm_file(argument.upper(), cache, progress)
    if re.fullmatch(r"[A-Za-z]{2}", argument):
        raise ValueError(f"{argument}: use the three-letter ISO code GADM uses (FRA, not FR)")
    raise ValueError(f"{argument}: expected a three-letter country code or a .gpkg file")


STAGING = """
CREATE TEMP TABLE region_load (
  id text, level smallint, name text, alt_names text[], type_label text, parent_id text,
  geometry geometry
) ON COMMIT DROP
"""

# Only regions whose whole chain of parents was kept (a lake's sub-regions go with it).
INSERT_REGIONS = """
INSERT INTO region (id, country, level, name, alt_names, type_label, parent_id, search_key,
                    geometry)
WITH RECURSIVE kept AS (
  SELECT * FROM region_load WHERE level = 0
  UNION ALL
  SELECT l.* FROM region_load l JOIN kept k ON l.parent_id = k.id
)
SELECT id, :country, level, name, alt_names, type_label, parent_id,
       vt_unaccent(lower('|' || array_to_string(name || alt_names, '|') || '|')),
       ST_Multi(CASE WHEN ST_IsValid(g) THEN g ELSE ST_CollectionExtract(ST_MakeValid(g), 3) END)
FROM (SELECT *, ST_SetSRID(geometry, 4326) AS g FROM kept) s
"""


def load_country(conn: Connection, path: Path) -> LoadReport:
    """Replace one country's regions (its tags go with them; derive_regions re-tags)."""
    rows = read_gadm(path)
    country = next(rows, None)
    if country is None or country.level != 0:
        raise ValueError(f"{path.name} has no country outline")
    with path.open("rb") as f:
        sha256 = hashlib.file_digest(f, "sha256").digest()
    report = LoadReport(country=country.id, name=country.name)
    conn.execute(delete(region_country).where(region_country.c.country == country.id))
    conn.execute(
        region_country.insert().values(
            country=country.id, name=country.name, source=path.name, sha256=sha256, regions=0
        )
    )
    conn.execute(text(STAGING))
    with (
        conn.connection.dbapi_connection.cursor() as cursor,
        cursor.copy(
            "COPY region_load (id, level, name, alt_names, type_label, parent_id, geometry) "
            "FROM STDIN"
        ) as copy,
    ):
        for r in (country, *rows):
            copy.write_row(
                (r.id, r.level, r.name, r.alt_names, r.type_label, r.parent_id, r.wkb.hex())
            )
    conn.execute(text(INSERT_REGIONS), {"country": country.id})
    report.levels = dict(
        conn.execute(
            text("SELECT level, count(*) FROM region WHERE country = :c GROUP BY 1 ORDER BY 1"),
            {"c": country.id},
        ).all()
    )
    report.regions = sum(report.levels.values())
    conn.execute(
        region_country.update()
        .where(region_country.c.country == country.id)
        .values(regions=report.regions)
    )
    conn.execute(text("DROP TABLE region_load"))
    return report


def load(
    engine, arguments: list[str], cache: Path, progress: Callable[[str], None] = print
) -> list[LoadReport]:
    """Load each country in its own transaction, then rebuild the tags once."""
    paths = [resolve(argument, cache, progress) for argument in arguments]
    reports = []
    for path in paths:
        progress(f"loading {path.name}")
        with engine.begin() as conn:
            reports.append(load_country(conn, path))
    with engine.begin() as conn:
        derive_regions(conn)
    return reports


def loaded_countries(conn: Connection) -> list[str]:
    return list(
        conn.execute(select(region_country.c.country).order_by(region_country.c.country))
        .scalars()
        .all()
    )


def remove(conn: Connection, codes: list[str]) -> list[str]:
    """Drop countries; their regions and tags go with them."""
    upper = [c.upper() for c in codes]
    return list(
        conn.execute(
            delete(region_country)
            .where(region_country.c.country.in_(upper))
            .returning(region_country.c.country)
        )
        .scalars()
        .all()
    )
