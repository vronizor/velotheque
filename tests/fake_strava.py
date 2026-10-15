import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
from sqlalchemy import select

from velotheque.config import Settings
from velotheque.strava.client import StravaClient
from velotheque.strava.sync import run_job, start_job
from velotheque.tables import sync_job

SETTINGS = Settings(
    strava_client_id="1", strava_client_secret="s", database_url="unused", _env_file=None
)

POLYLINE = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
BASE = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def summary(activity_id: int, day: int = 0, **overrides) -> dict:
    start = BASE + timedelta(days=day)
    payload = {
        "id": activity_id,
        "name": f"Ride {activity_id}",
        "sport_type": "Ride",
        "type": "Ride",
        "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start_date_local": (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timezone": "(GMT+01:00) Europe/Paris",
        "distance": 42000.0,
        "moving_time": 5400,
        "elapsed_time": 6000,
        "total_elevation_gain": 512.0,
        "gear_id": "b1",
        "trainer": False,
        "total_photo_count": 0,
        "kudos_count": 0,
        "map": {"summary_polyline": POLYLINE},
    }
    payload.update(overrides)
    return payload


def streams_for(n: int = 50, gap_at: int | None = None) -> dict:
    latlng = []
    for i in range(n):
        lat, lng = 48.85 + i * 0.0005, 2.35 + i * 0.0005
        if gap_at is not None and i >= gap_at:
            lat += 0.05
        latlng.append([lat, lng])
    return {
        "latlng": {"data": latlng},
        "time": {"data": list(range(0, n * 5, 5))},
        "distance": {"data": [i * 60.0 for i in range(n)]},
        "altitude": {"data": [35.0 + i for i in range(n)]},
        "moving": {"data": [True] * n},
    }


class FakeStrava:
    def __init__(self):
        self.athlete = {"id": 99, "bikes": [{"id": "b1", "name": "Gravel", "retired": False}]}
        self.gear = {"b2": {"id": "b2", "name": "Old road bike", "retired": True}}
        self.activities: dict[int, dict] = {}
        self.descriptions: dict[int, str] = {}
        self.streams: dict[int, dict | None] = {}
        self.calls: list[str] = []
        self.read_limit = (100, 1000)
        self.read_usage = [0, 0]
        self.tokens_issued = 0

    def add(self, payload: dict, streams: dict | None | bool = True) -> None:
        self.activities[payload["id"]] = payload
        if streams is True:
            streams = streams_for()
        self.streams[payload["id"]] = streams

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _json(self, status: int, body) -> httpx.Response:
        self.read_usage[0] += 1
        self.read_usage[1] += 1
        headers = {
            "X-ReadRateLimit-Limit": f"{self.read_limit[0]},{self.read_limit[1]}",
            "X-ReadRateLimit-Usage": f"{self.read_usage[0]},{self.read_usage[1]}",
        }
        return httpx.Response(status, json=body, headers=headers)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path.endswith("/oauth/token"):
            self.tokens_issued += 1
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["refresh_token"]
            return httpx.Response(
                200,
                json={
                    "access_token": f"access-{self.tokens_issued}",
                    "refresh_token": f"refresh-{self.tokens_issued}",
                    "expires_at": int((datetime.now(UTC) + timedelta(hours=6)).timestamp()),
                },
            )
        assert request.headers["Authorization"].startswith("Bearer ")
        parts = path.split("/api/v3", 1)[1].strip("/").split("/")
        if parts == ["athlete"]:
            return self._json(200, self.athlete)
        if parts[0] == "gear":
            return self._json(200, self.gear[parts[1]])
        if parts == ["athlete", "activities"]:
            params = request.url.params
            after = int(params.get("after", 0))
            page, per_page = int(params["page"]), int(params["per_page"])
            rows = sorted(
                (a for a in self.activities.values() if _epoch(a["start_date"]) > after),
                key=lambda a: a["start_date"],
            )
            return self._json(200, rows[(page - 1) * per_page : page * per_page])
        if parts[0] == "activities" and len(parts) == 2:
            activity_id = int(parts[1])
            detail = json.loads(json.dumps(self.activities[activity_id]))
            detail["description"] = self.descriptions.get(activity_id, "")
            return self._json(200, detail)
        if parts[0] == "activities" and parts[2] == "streams":
            body = self.streams.get(int(parts[1]))
            if body is None:
                return self._json(404, {"message": "Record Not Found", "errors": []})
            return self._json(200, body)
        raise AssertionError(f"unexpected request {path}")


def _epoch(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def sync(db, fake, kind="sync", source_id=None):
    client = StravaClient(db, SETTINGS, http=httpx.Client(transport=fake.transport()))
    job_id = start_job(db, kind, source_id)
    run_job(db, SETTINGS, client, job_id)
    with db.connect() as conn:
        return conn.execute(select(sync_job).where(sync_job.c.id == job_id)).one()
