from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, Request
from sqlalchemy import Connection, select

from velotheque import backup
from velotheque.config import get_settings
from velotheque.db import get_engine
from velotheque.strava.client import StravaClient
from velotheque.strava.oauth import load_auth
from velotheque.strava.sync import JobAlreadyRunning, run_job, start_job
from velotheque.tables import sync_job
from velotheque.web.deps import get_conn, templates

router = APIRouter()
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sync")


def submit(kind: str, activity_source_id: int | None = None) -> int | None:
    engine, settings = get_engine(), get_settings()
    try:
        job_id = start_job(engine, kind, activity_source_id)
    except JobAlreadyRunning:
        return None
    executor.submit(run_job, engine, settings, StravaClient(engine, settings), job_id)
    return job_id


def latest_job(conn: Connection):
    return conn.execute(select(sync_job).order_by(sync_job.c.id.desc()).limit(1)).first()


def render_widget(request: Request, conn: Connection, job=None, finished_event=False):
    job = job or latest_job(conn)
    response = templates.TemplateResponse(
        request,
        "_sync.html",
        {"job": job, "connected": load_auth(conn) is not None},
    )
    if finished_event:
        response.headers["HX-Trigger"] = "syncFinished"
    return response


@router.get("/backup/indicator")
def backup_indicator(request: Request, conn: Connection = Depends(get_conn)):
    return templates.TemplateResponse(request, "_backup.html", {"backup": backup.state(conn)})


@router.get("/jobs/latest")
def job_latest(request: Request, conn: Connection = Depends(get_conn)):
    return render_widget(request, conn)


@router.post("/jobs/sync")
def job_sync(request: Request, conn: Connection = Depends(get_conn)):
    if load_auth(conn) is not None:
        submit("sync")
    return render_widget(request, conn)


@router.get("/jobs/{job_id}")
def job_status(job_id: int, request: Request, conn: Connection = Depends(get_conn)):
    job = conn.execute(select(sync_job).where(sync_job.c.id == job_id)).one()
    return render_widget(request, conn, job, finished_event=job.status != "running")
