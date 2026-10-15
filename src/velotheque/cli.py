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

    with get_engine().begin() as conn:
        ids = normalize_all(conn, get_settings().sport_types)
        derive_activities(conn, ids)
    print(f"normalized {len(ids)} activities")


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
    sub.add_parser("normalize", help="re-normalize every activity from raw data").set_defaults(
        func=cmd_normalize
    )
    sub.add_parser("rebuild-derived", help="drop and rebuild derived tables").set_defaults(
        func=cmd_rebuild_derived
    )
    args = parser.parse_args(argv)
    args.func(args)
