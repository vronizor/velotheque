from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import Engine

from velotheque.config import Settings
from velotheque.strava import oauth
from velotheque.strava.ratelimit import RateBudget

STREAM_KEYS = "latlng,altitude,time,distance,moving"
REFRESH_MARGIN = timedelta(minutes=5)


class StravaClient:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        http: httpx.Client | None = None,
        budget: RateBudget | None = None,
    ):
        self.engine = engine
        self.settings = settings
        self.http = http or httpx.Client(timeout=30)
        self.budget = budget or RateBudget(margin=settings.rate_limit_margin)
        self._access_token: str | None = None
        self._expires_at: datetime | None = None

    def _token(self) -> str:
        now = datetime.now(UTC)
        if self._access_token and self._expires_at and self._expires_at - now > REFRESH_MARGIN:
            return self._access_token
        with self.engine.begin() as conn:
            auth = oauth.load_auth(conn)
            if auth is None:
                raise oauth.NotConnected("Strava is not connected")
            access, expires = auth.access_token, auth.expires_at
            if expires - now <= REFRESH_MARGIN:
                token = oauth.refresh(self.settings, self.http, auth.refresh_token)
                oauth.save_tokens(conn, token)
                access = token["access_token"]
                expires = datetime.fromtimestamp(token["expires_at"], UTC)
        self._access_token, self._expires_at = access, expires
        return access

    def get(self, path: str, params: dict | None = None, allow_404: bool = False):
        self.budget.check()
        response = self.http.get(
            f"{self.settings.strava_api_base}{path}",
            params=params,
            headers={"Authorization": f"Bearer {self._token()}"},
        )
        self.budget.update(response.headers)
        if response.status_code == 429:
            raise self.budget.exhausted()
        if response.status_code == 401:
            raise oauth.NotConnected("Strava rejected the access token; reconnect in Settings")
        if response.status_code == 404 and allow_404:
            return response
        response.raise_for_status()
        return response

    def athlete(self) -> dict:
        return self.get("/athlete").json()

    def gear(self, gear_id: str) -> dict:
        return self.get(f"/gear/{gear_id}").json()

    def list_activities(self, after: int, page: int, per_page: int = 200) -> list[dict]:
        params = {"after": after, "page": page, "per_page": per_page}
        return self.get("/athlete/activities", params).json()

    def activity(self, activity_id: int) -> dict:
        return self.get(f"/activities/{activity_id}", {"include_all_efforts": "false"}).json()

    def streams(self, activity_id: int) -> tuple[int, object]:
        response = self.get(
            f"/activities/{activity_id}/streams",
            {"keys": STREAM_KEYS, "key_by_type": "true"},
            allow_404=True,
        )
        return response.status_code, response.json()
