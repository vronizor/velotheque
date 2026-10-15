import io
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import polars as pl
from sqlalchemy import Engine

from velotheque.config import Settings
from velotheque.derive import derive_activities
from velotheque.exportrow import gear_id, sport_type
from velotheque.normalize import normalize_activity
from velotheque.raw import store_raw
from velotheque.tables import raw_export_activity

EXPORT_DIR = "strava-export"


def read_activities_csv(data: bytes) -> pl.DataFrame:
    """All columns as strings. Repeated headers (Distance, Elapsed Time, …) get polars'
    `_duplicated_N` suffix; the second Distance is in metres, the first in km."""
    return pl.read_csv(io.BytesIO(data), infer_schema_length=0)


def _extract(archive: zipfile.ZipFile, member: str, target_root: Path) -> Path:
    relative = PurePosixPath(member)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe path in export: {member}")
    target = target_root.joinpath(*relative.parts)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    return target


@dataclass
class ImportReport:
    rows: int = 0
    new_or_changed: int = 0
    files: int = 0
    cycling: int = 0
    normalized: int = 0
    missing_files: list[int] = field(default_factory=list)
    unmatched_gear: list[tuple[int, str, str]] = field(default_factory=list)


def import_export(
    engine: Engine,
    settings: Settings,
    zip_path: Path,
    progress: Callable[[str], None] = lambda _: None,
) -> ImportReport:
    report = ImportReport()
    export_name = zip_path.stem
    root = settings.data_dir / EXPORT_DIR / export_name
    cycling: list[int] = []
    with zipfile.ZipFile(zip_path) as archive:
        members = set(archive.namelist())
        frame = read_activities_csv(archive.read("activities.csv"))
        for row in frame.iter_rows(named=True):
            report.rows += 1
            source_id = int(row["Activity ID"])
            member = row.get("Filename")
            file_path = None
            if member and member in members:
                _extract(archive, member, root)
                file_path = f"{EXPORT_DIR}/{export_name}/{member}"
                report.files += 1
            elif member:
                report.missing_files.append(source_id)
            with engine.begin() as conn:
                if store_raw(
                    conn,
                    raw_export_activity,
                    row,
                    {"source_id": source_id},
                    {"export_name": export_name, "file_path": file_path},
                ):
                    report.new_or_changed += 1
            if sport_type(row.get("Activity Type")) in settings.sport_types:
                cycling.append(source_id)
                if row.get("Activity Gear") and not gear_id(row):
                    report.unmatched_gear.append(
                        (source_id, row.get("Activity Name") or "", row["Activity Gear"])
                    )
    report.cycling = len(cycling)
    progress(f"stored {report.rows} rows ({report.new_or_changed} new or changed)")
    for i, source_id in enumerate(cycling, 1):
        with engine.begin() as conn:
            activity_id = normalize_activity(conn, source_id, settings)
            if activity_id is not None:
                derive_activities(conn, [activity_id])
                report.normalized += 1
        if i % 50 == 0:
            progress(f"normalized {i}/{len(cycling)} rides")
    return report
