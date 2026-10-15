from datetime import UTC, datetime

DATE_FORMAT = "%b %d, %Y, %I:%M:%S %p"


def sport_type(export_type: str | None) -> str:
    return (export_type or "").replace(" ", "").replace("-", "")


def media_paths(value: str | None) -> list[str]:
    return [p for p in (value or "").split("|") if p]


def gear_id(row: dict) -> str | None:
    bike = row.get("Bike")
    return f"b{int(float(bike))}" if bike else None


def _float(row: dict, key: str) -> float | None:
    value = row.get(key)
    return float(value) if value not in (None, "") else None


def distance_m(row: dict) -> float:
    metres = _float(row, "Distance_duplicated_0")
    if metres is not None:
        return metres
    return (_float(row, "Distance") or 0.0) * 1000


def as_api_payload(row: dict) -> dict:
    """Shape an export row like a Strava activity summary so normalize can treat it alike.
    The export has no timezone; Activity Date is UTC, so local time stays UTC until the API
    supplies the real one."""
    start = datetime.strptime(row["Activity Date"], DATE_FORMAT).replace(tzinfo=UTC)
    sport = sport_type(row.get("Activity Type"))
    return {
        "name": row.get("Activity Name") or "",
        "sport_type": sport,
        "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start_date_local": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timezone": None,
        "distance": distance_m(row),
        "moving_time": int(_float(row, "Moving Time") or _float(row, "Elapsed Time") or 0),
        "elapsed_time": int(_float(row, "Elapsed Time") or 0),
        "total_elevation_gain": _float(row, "Elevation Gain") or 0.0,
        "gear_id": gear_id(row),
        "gear_name": row.get("Activity Gear"),
        "trainer": sport == "VirtualRide",
        "total_photo_count": len(media_paths(row.get("Media"))),
        "description": row.get("Activity Description"),
    }
