import argparse
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    if url:
        config.set_main_option("sqlalchemy.url", url)
    return config


def cmd_migrate(args) -> None:
    command.upgrade(alembic_config(), "head")


def cmd_sync(args) -> None:
    from sqlalchemy import select

    from velotheque.config import get_settings
    from velotheque.db import get_engine
    from velotheque.strava.client import StravaClient
    from velotheque.strava.sync import run_job, start_job
    from velotheque.tables import sync_job

    engine, settings = get_engine(), get_settings()
    kind = "refresh" if args.activity else "sync"
    job_id = start_job(engine, kind, args.activity)
    run_job(engine, settings, StravaClient(engine, settings), job_id)
    with engine.connect() as conn:
        job = conn.execute(select(sync_job).where(sync_job.c.id == job_id)).one()
    print(f"job {job.id}: {job.status} {job.counts}")
    if job.error:
        print(job.error)
    if job.resumes_after:
        print(f"rate limited; run again after {job.resumes_after:%Y-%m-%d %H:%M %Z}")


def cmd_normalize(args) -> None:
    from velotheque.config import get_settings
    from velotheque.db import get_engine
    from velotheque.derive import derive_activities
    from velotheque.normalize import normalize_all
    from velotheque.photos import materialize_photos
    from velotheque.tables import activity

    settings = get_settings()
    with get_engine().begin() as conn:
        ids = normalize_all(conn, settings, force_track=args.force_tracks)
        derive_activities(conn, ids)
        rows = conn.execute(
            activity.select().with_only_columns(activity.c.id, activity.c.source_id)
        ).all()
        photos = sum(materialize_photos(conn, r.id, r.source_id, settings) for r in rows)
    print(f"normalized {len(ids)} activities, {photos} photos")


def cmd_import_export(args) -> None:
    from velotheque.config import get_settings
    from velotheque.db import get_engine
    from velotheque.export import import_export

    report = import_export(get_engine(), get_settings(), Path(args.zip), progress=print)
    print(
        f"{report.rows} activities in the export, {report.cycling} rides normalized "
        f"({report.normalized} ok), {report.files} track files, {report.photos} photos/videos"
    )
    if report.missing_files:
        print(f"track file missing from the zip for: {report.missing_files}")
    if report.unmatched_gear:
        print("rides whose gear could not be matched to a Strava bike (review manually):")
        for source_id, name, gear in report.unmatched_gear:
            print(f"  {source_id}  {name}  [{gear}]")


def cmd_backup_status(args) -> None:
    import json

    from velotheque.backup import record
    from velotheque.db import get_engine

    details = json.loads(args.details) if args.details else {}
    with get_engine().begin() as conn:
        record(conn, ok=args.ok, error=args.error, details=details)
    print("recorded backup", "success" if args.ok else f"failure: {args.error}")


def cmd_rebuild_derived(args) -> None:
    from velotheque.db import get_engine
    from velotheque.derive import rebuild_derived

    with get_engine().begin() as conn:
        rebuild_derived(conn)
    print("derived tables rebuilt")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="velotheque")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database migrations").set_defaults(func=cmd_migrate)
    sync = sub.add_parser("sync", help="sync from Strava (same as the Sync now button)")
    sync.add_argument("--activity", type=int, help="refresh a single Strava activity id")
    sync.set_defaults(func=cmd_sync)
    normalize = sub.add_parser("normalize", help="re-normalize every activity from raw data")
    normalize.add_argument(
        "--force-tracks", action="store_true", help="re-parse export track files too"
    )
    normalize.set_defaults(func=cmd_normalize)
    export = sub.add_parser("import-export", help="import a Strava bulk export zip")
    export.add_argument("zip", help="path to export_XXXX.zip")
    export.set_defaults(func=cmd_import_export)
    status = sub.add_parser("backup-status", help="record the result of a host backup run")
    outcome = status.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--ok", action="store_true", help="the run succeeded")
    outcome.add_argument("--error", help="the run failed with this message")
    status.add_argument("--details", help="JSON object with counts, sizes, file names")
    status.set_defaults(func=cmd_backup_status)
    sub.add_parser("rebuild-derived", help="drop and rebuild derived tables").set_defaults(
        func=cmd_rebuild_derived
    )
    args = parser.parse_args(argv)
    args.func(args)
