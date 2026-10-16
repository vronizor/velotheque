import hashlib
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates
from sqlalchemy import Connection

from velotheque.db import get_engine

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
STATIC_DIR = Path(__file__).parent / "static"
# Appended to asset URLs so browsers fetch the new CSS/JS after a deploy.
templates.env.globals["static_version"] = hashlib.sha256(
    b"".join(p.read_bytes() for p in sorted(STATIC_DIR.glob("*.*")))
).hexdigest()[:10]


def get_conn() -> Iterator[Connection]:
    with get_engine().connect() as conn:
        yield conn


def km(meters) -> str:
    return f"{(meters or 0) / 1000:,.1f}".replace(",", " ")


def whole(value) -> str:
    return f"{round(value or 0):,}".replace(",", " ")


def duration(seconds) -> str:
    minutes = round((seconds or 0) / 60)
    return f"{minutes // 60}:{minutes % 60:02d}"


def hours(seconds) -> str:
    return f"{(seconds or 0) / 3600:,.0f}".replace(",", " ")


def percent(fraction) -> str:
    if fraction is None:
        return "–"
    value = fraction * 100
    return f"{value:.0f}%" if value >= 1 or value == 0 else "<1%"


def when(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return value.strftime(fmt) if value else ""


def ago(delta) -> str:
    if delta is None:
        return ""
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h"
    return f"{minutes // (24 * 60)} d"


templates.env.filters.update(
    percent=percent, km=km, whole=whole, duration=duration, hours=hours, when=when, ago=ago
)
