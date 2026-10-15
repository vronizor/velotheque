from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

HEADER_FAMILIES = (
    ("X-RateLimit-Limit", "X-RateLimit-Usage"),
    ("X-ReadRateLimit-Limit", "X-ReadRateLimit-Usage"),
)


class RateLimited(Exception):
    def __init__(self, resumes_after: datetime):
        super().__init__(f"Strava rate limit reached; resumes after {resumes_after.isoformat()}")
        self.resumes_after = resumes_after


def _pair(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        short, daily = (int(v.strip()) for v in value.split(","))
    except ValueError:
        return None
    return short, daily


def next_quarter_hour(now: datetime) -> datetime:
    base = now.replace(second=0, microsecond=0)
    return base + timedelta(minutes=15 - base.minute % 15)


def next_utc_midnight(now: datetime) -> datetime:
    now = now.astimezone(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


@dataclass
class RateBudget:
    margin: int = 3
    short_remaining: int | None = None
    daily_remaining: int | None = None
    seen: dict[str, tuple[int, int]] = field(default_factory=dict)

    def update(self, headers) -> None:
        shorts, dailies = [], []
        for limit_name, usage_name in HEADER_FAMILIES:
            limit, usage = _pair(headers.get(limit_name)), _pair(headers.get(usage_name))
            if limit is None or usage is None:
                continue
            self.seen[limit_name] = limit
            self.seen[usage_name] = usage
            shorts.append(limit[0] - usage[0])
            dailies.append(limit[1] - usage[1])
        if shorts:
            self.short_remaining = min(shorts)
            self.daily_remaining = min(dailies)

    def check(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        if self.daily_remaining is not None and self.daily_remaining <= self.margin:
            raise RateLimited(next_utc_midnight(now))
        if self.short_remaining is not None and self.short_remaining <= self.margin:
            raise RateLimited(next_quarter_hour(now))

    def exhausted(self, now: datetime | None = None) -> RateLimited:
        now = now or datetime.now(UTC)
        if self.daily_remaining is not None and self.daily_remaining <= 0:
            return RateLimited(next_utc_midnight(now))
        return RateLimited(next_quarter_hour(now))
