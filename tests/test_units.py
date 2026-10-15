from datetime import UTC, datetime

import pytest

from velotheque.normalize import parse_local, parse_timezone, split_segments
from velotheque.strava.ratelimit import RateBudget, RateLimited


def test_budget_uses_tightest_family():
    budget = RateBudget(margin=3)
    budget.update(
        {
            "X-RateLimit-Limit": "200,2000",
            "X-RateLimit-Usage": "10,100",
            "X-ReadRateLimit-Limit": "100,1000",
            "X-ReadRateLimit-Usage": "95,200",
        }
    )
    assert (budget.short_remaining, budget.daily_remaining) == (5, 800)
    budget.check()


def test_budget_stops_at_margin_until_next_quarter_hour():
    budget = RateBudget(margin=3)
    budget.update({"X-ReadRateLimit-Limit": "100,1000", "X-ReadRateLimit-Usage": "97,200"})
    with pytest.raises(RateLimited) as exc:
        budget.check(datetime(2026, 10, 14, 9, 7, 30, tzinfo=UTC))
    assert exc.value.resumes_after == datetime(2026, 10, 14, 9, 15, tzinfo=UTC)


def test_daily_exhaustion_resumes_at_utc_midnight():
    budget = RateBudget(margin=3)
    budget.update({"X-ReadRateLimit-Limit": "100,1000", "X-ReadRateLimit-Usage": "1,999"})
    with pytest.raises(RateLimited) as exc:
        budget.check(datetime(2026, 10, 14, 21, 0, tzinfo=UTC))
    assert exc.value.resumes_after == datetime(2026, 10, 15, tzinfo=UTC)


def test_missing_headers_do_not_block():
    budget = RateBudget()
    budget.update({})
    budget.check()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("(GMT+01:00) Europe/Paris", "Europe/Paris"),
        ("(GMT+01:00) Europe/Madrid", "Europe/Madrid"),
        ("(GMT-03:00) America/Argentina/Buenos_Aires", "America/Argentina/Buenos_Aires"),
        (None, None),
    ],
)
def test_parse_timezone(raw, expected):
    assert parse_timezone(raw) == expected


def test_parse_local_drops_fake_utc_marker():
    assert parse_local("2026-07-01T07:30:00Z") == datetime(2026, 7, 1, 7, 30)


def test_split_segments_drops_single_points_and_splits_on_gaps():
    points = [[45.0, 6.0], [45.0001, 6.0001], [45.1, 6.1], [45.5, 6.5], [45.5001, 6.5001]]
    assert split_segments(points) == [points[:2], points[3:]]
