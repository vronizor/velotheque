from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert

from velotheque.config import Settings
from velotheque.tables import strava_auth

REQUIRED_SCOPES = {"activity:read_all", "profile:read_all"}


class NotConnected(Exception):
    pass


def authorize_url(settings: Settings, state: str) -> str:
    query = urlencode(
        {
            "client_id": settings.strava_client_id,
            "redirect_uri": settings.redirect_uri,
            "response_type": "code",
            "approval_prompt": "auto",
            "scope": settings.strava_scopes,
            "state": state,
        }
    )
    return f"{settings.strava_oauth_base}/authorize?{query}"


def missing_scopes(granted: str) -> set[str]:
    return REQUIRED_SCOPES - set(granted.split(","))


def _token_request(settings: Settings, http: httpx.Client, **data) -> dict:
    response = http.post(
        f"{settings.strava_oauth_base}/token",
        data={
            "client_id": settings.strava_client_id,
            "client_secret": settings.strava_client_secret,
            **data,
        },
    )
    response.raise_for_status()
    return response.json()


def exchange_code(settings: Settings, http: httpx.Client, code: str) -> dict:
    return _token_request(settings, http, code=code, grant_type="authorization_code")


def refresh(settings: Settings, http: httpx.Client, refresh_token: str) -> dict:
    return _token_request(settings, http, refresh_token=refresh_token, grant_type="refresh_token")


def save_tokens(conn: Connection, token: dict, athlete_id: int | None = None, scope=None) -> None:
    values = {
        "access_token": token["access_token"],
        "refresh_token": token["refresh_token"],
        "expires_at": datetime.fromtimestamp(token["expires_at"], UTC),
        "updated_at": datetime.now(UTC),
    }
    if athlete_id is None:
        conn.execute(strava_auth.update().where(strava_auth.c.id == 1).values(**values))
        return
    stmt = insert(strava_auth).values(id=1, athlete_id=athlete_id, scope=scope, **values)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[strava_auth.c.id],
            set_={**values, "athlete_id": athlete_id, "scope": scope},
        )
    )


def load_auth(conn: Connection):
    return conn.execute(select(strava_auth).where(strava_auth.c.id == 1)).first()
