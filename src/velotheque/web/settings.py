import secrets
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import Connection, select

from velotheque import backup, queries
from velotheque.config import get_settings
from velotheque.db import get_engine
from velotheque.strava import oauth
from velotheque.tables import sync_job
from velotheque.web.deps import get_conn, templates

router = APIRouter()
STATE_COOKIE = "strava_oauth_state"


@router.get("/settings")
def settings_page(request: Request, conn: Connection = Depends(get_conn)):
    auth = oauth.load_auth(conn)
    settings = get_settings()
    jobs = conn.execute(select(sync_job).order_by(sync_job.c.id.desc()).limit(25)).all()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "auth": auth,
            "missing_scopes": sorted(oauth.missing_scopes(auth.scope)) if auth else [],
            "jobs": jobs,
            "export": queries.export_status(conn, settings.sport_types),
            "regions": queries.region_status(conn),
            "backup": backup.state(conn),
            "backups": backup.history(conn),
            "configured": bool(settings.strava_client_id and settings.strava_client_secret),
            "redirect_uri": settings.redirect_uri,
            "message": request.query_params.get("message"),
            "now": datetime.now(UTC),
        },
    )


@router.get("/strava/connect")
def strava_connect():
    state = secrets.token_urlsafe(24)
    response = RedirectResponse(oauth.authorize_url(get_settings(), state), status_code=303)
    response.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax")
    return response


def _back(message: str) -> RedirectResponse:
    settings = get_settings()
    query = urlencode({"message": message})
    response = RedirectResponse(f"{settings.base_url.rstrip('/')}/settings?{query}", 303)
    response.delete_cookie(STATE_COOKIE)
    return response


@router.get("/strava/callback")
def strava_callback(request: Request):
    params = request.query_params
    if params.get("error"):
        return _back(f"Strava returned an error: {params['error']}")
    expected = request.cookies.get(STATE_COOKIE)
    if not expected or not secrets.compare_digest(expected, params.get("state", "")):
        return _back("OAuth state mismatch; start again from this page")
    scope = params.get("scope", "")
    settings = get_settings()
    with httpx.Client(timeout=30) as http:
        token = oauth.exchange_code(settings, http, params["code"])
    with get_engine().begin() as conn:
        oauth.save_tokens(conn, token, athlete_id=token["athlete"]["id"], scope=scope)
    missing = oauth.missing_scopes(scope)
    if missing:
        return _back(f"Connected, but without {', '.join(sorted(missing))}; reconnect and allow")
    return _back("Connected to Strava")
