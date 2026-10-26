import csv
import gzip
import io
import zipfile
from datetime import datetime

import pytest
from sqlalchemy import func, select

from tests.fake_strava import SETTINGS, jpeg_bytes, summary, sync
from velotheque.export import import_export
from velotheque.exportrow import as_api_payload
from velotheque.tables import (
    activity,
    activity_photo,
    activity_track,
    bike,
    companion,
    raw_export_activity,
)
from velotheque.trackfiles import parse_gpx, parse_tcx

HEADER = [
    "Activity ID", "Activity Date", "Activity Name", "Activity Type", "Activity Description",
    "Elapsed Time", "Distance", "Activity Gear", "Filename", "Elapsed Time", "Moving Time",
    "Distance", "Elevation Gain", "Bike", "Media",
]  # fmt: skip


def gpx(n=30, lat=45.0, lng=6.0) -> bytes:
    points = "".join(
        f'<trkpt lat="{lat + i * 0.0004}" lon="{lng + i * 0.0004}"><ele>{500 + i}</ele>'
        f"<time>2025-06-01T06:{i // 60:02d}:{i % 60:02d}Z</time></trkpt>"
        for i in range(n)
    )
    return (
        '<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
        f"{points}</trkseg></trk></gpx>"
    ).encode()


def tcx(n=30) -> bytes:
    points = "".join(
        f"<Trackpoint><Time>2025-06-02T07:00:{i:02d}Z</Time><Position>"
        f"<LatitudeDegrees>{41.4 + i * 0.0004}</LatitudeDegrees>"
        f"<LongitudeDegrees>{2.17 + i * 0.0004}</LongitudeDegrees></Position>"
        f"<AltitudeMeters>{10 + i}</AltitudeMeters><DistanceMeters>{i * 50.0}</DistanceMeters>"
        "</Trackpoint>"
        for i in range(n)
    )
    xmlns = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
    return (
        f'          <?xml version="1.0"?><TrainingCenterDatabase xmlns="{xmlns}"><Activities>'
        f"<Activity><Lap><Track>{points}</Track></Lap></Activity></Activities>"
        "</TrainingCenterDatabase>"
    ).encode()


ROWS = [
    # id, date (UTC), name, type, gear, file, km, bike, media
    (1, "Jun 1, 2025, 6:00:00 AM", "Col de la Forclaz", "Ride", "Le Capitaine",
     "activities/101.gpx", "12.5", "1274389.0", "||media/a.jpg|media/b.mp4"),
    (2, "Jun 2, 2025, 7:00:00 AM", "Barcelona loop", "Ride", "", "activities/102.tcx.gz",
     "40.0", "", ""),
    (3, "Jun 3, 2025, 8:00:00 AM", "Morning run", "Run", "Mizuno", "activities/103.gpx",
     "8.0", "", ""),
    (4, "Jun 4, 2025, 9:00:00 AM", "Zwift", "Virtual Ride", "Trainer bike", "", "20.0", "", ""),
]  # fmt: skip


def make_export(path, rows=ROWS, description="Lovely day", with_media=True):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADER)
    for sid, date, name, kind, gear, filename, km, bike_id, media in rows:
        metres = str(float(km) * 1000)
        writer.writerow(
            [sid, date, name, kind, description, "4000", km, gear, filename, "4000.0", "3600.0",
             metres, "321.0", bike_id, media]
        )  # fmt: skip
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("activities.csv", buffer.getvalue())
        archive.writestr("activities/101.gpx", gpx())
        archive.writestr("activities/102.tcx.gz", gzip.compress(tcx()))
        archive.writestr("activities/103.gpx", gpx(lat=46.0))
        if with_media:
            archive.writestr("media/a.jpg", jpeg_bytes())
            archive.writestr("media/b.mp4", b"not really a video")
    return path


@pytest.fixture
def settings(tmp_path):
    return SETTINGS.model_copy(update={"data_dir": tmp_path / "data"})


def rides(db):
    with db.connect() as conn:
        return {
            r.source_id: r
            for r in conn.execute(
                select(
                    activity, activity_track.c.track_source, bike.c.name.label("bike_name")
                ).select_from(
                    activity.outerjoin(activity_track).outerjoin(
                        bike, bike.c.id == activity.c.bike_id
                    )
                )
            )
        }


def test_parsers_handle_namespaces_and_leading_whitespace():
    track = parse_gpx(gpx(5))
    assert track.latlng[0] == [45.0, 6.0] and track.time_s() == [0, 1, 2, 3, 4]
    assert track.altitudes() == [500.0, 501.0, 502.0, 503.0, 504.0]
    assert track.distances()[0] == 0 and track.distances()[-1] > 200
    track = parse_tcx(tcx(3))
    assert track.distances() == [0.0, 50.0, 100.0]


def test_export_row_mapping():
    payload = as_api_payload(
        dict(
            zip(
                ["Activity ID", "Activity Date", "Activity Name", "Activity Type", "Bike",
                 "Distance", "Distance_duplicated_0", "Moving Time", "Elapsed Time", "Media"],
                ["9", "Aug 15, 2026, 5:47:04 AM", "x", "Virtual Ride", "14624685.0", "76.87",
                 "76872.0", "17267.0", "20493", "||media/a.jpg|media/b.jpg"],
                strict=True,
            )
        )
    )  # fmt: skip
    assert payload["start_date"] == "2026-08-15T05:47:04Z"
    assert payload["sport_type"] == "VirtualRide" and payload["trainer"] is True
    assert payload["gear_id"] == "b14624685"
    assert payload["distance"] == 76872.0 and payload["moving_time"] == 17267
    assert payload["total_photo_count"] == 2


def test_import_builds_rides_from_export_alone(db, settings, tmp_path):
    report = import_export(db, settings, make_export(tmp_path / "export_1.zip"))

    assert (report.rows, report.kept, report.normalized) == (4, 4, 4)
    found = rides(db)
    assert set(found) == {1, 2, 3, 4}
    assert found[3].type == "Run" and found[3].bike_name is None
    assert found[3].track_source == "export"
    assert found[1].track_source == "export" and found[2].track_source == "export"
    assert found[4].track_source is None and found[4].trainer
    assert found[1].bike_name == "Le Capitaine" and found[1].has_photos
    assert found[1].description == "Lovely day"
    assert found[2].distance_m == 40000.0
    assert found[1].start_at_local == datetime(2025, 6, 1, 6, 0)
    assert report.unmatched_gear == [(4, "Zwift", "Trainer bike")]
    assert (settings.data_dir / "strava-export/export_1/activities/101.gpx").exists()


def test_reimport_is_idempotent(db, settings, tmp_path):
    path = make_export(tmp_path / "export_1.zip")
    import_export(db, settings, path)
    report = import_export(db, settings, path)
    assert report.new_or_changed == 0
    with db.connect() as conn:
        assert conn.execute(select(func.count()).select_from(raw_export_activity)).scalar() == 4
        assert conn.execute(select(func.count()).select_from(activity)).scalar() == 4


def test_api_wins_for_metadata_and_export_saves_api_calls(db, settings, tmp_path, strava):
    import_export(db, settings, make_export(tmp_path / "export_1.zip"))
    strava.add(
        summary(
            1,
            name="Forclaz (renamed on Strava)",
            start_date="2025-06-01T06:00:00Z",
            start_date_local="2025-06-01T08:00:00Z",
        )
    )

    job = sync(db, strava, settings=settings)

    assert job.status == "succeeded", job.error
    ride = rides(db)[1]
    assert ride.name == "Forclaz (renamed on Strava)"
    assert ride.start_at_local == datetime(2025, 6, 1, 8, 0)
    assert ride.timezone == "Europe/Paris"
    assert ride.description == "Lovely day"
    assert ride.track_source == "export"
    assert [c for c in strava.calls if "/activities/1" in c] == []


def test_unsafe_member_paths_are_rejected(db, settings, tmp_path):
    rows = [(*ROWS[0][:5], "../../evil.gpx", *ROWS[0][6:])]
    path = make_export(tmp_path / "export_2.zip", rows=rows)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("../../evil.gpx", gpx())
    with pytest.raises(ValueError, match="unsafe path"):
        import_export(db, settings, path)


class _Field:
    def __init__(self, raw_value, value=None):
        self.raw_value, self.value = raw_value, value


class _Frame:
    class def_mesg:
        endian = ">"

    def __init__(self, **fields):
        self.fields = fields

    def has_field(self, name):
        return name in self.fields

    def get_field(self, name):
        return self.fields[name]


def test_byte_typed_fit_fields_are_decoded_big_endian():
    from velotheque.trackfiles import SEMICIRCLE, _fit_value

    frame = _Frame(
        position_lat=_Field((28, 203, 32, 0)),
        position_long=_Field((127, 255, 255, 255)),
        timestamp=_Field((63, 73, 162, 17)),
        altitude=_Field((43, 87)),
        distance=_Field(12345, 123.45),
    )
    assert round(_fit_value(frame, "position_lat") * SEMICIRCLE, 4) == 40.4908
    assert _fit_value(frame, "position_long") is None
    assert _fit_value(frame, "timestamp").isoformat() == "2023-08-24T05:43:45+00:00"
    assert _fit_value(frame, "altitude") == 0x2B57 / 5 - 500
    assert _fit_value(frame, "distance") == 123.45
    assert _fit_value(frame, "heart_rate") is None


def photos(db):
    with db.connect() as conn:
        return conn.execute(
            select(activity_photo).order_by(activity_photo.c.activity_id, activity_photo.c.position)
        ).all()


def test_export_media_become_photos_with_thumbnails(db, settings, tmp_path):
    path = make_export(tmp_path / "export_1.zip")
    report = import_export(db, settings, path)

    found = photos(db)
    assert report.photos == 2
    assert [(p.unique_id, p.kind, p.source) for p in found] == [
        ("a", "image", "export"),
        ("b", "video", "export"),
    ]
    assert found[0].file_path == "photos/1/a.jpg" and (found[0].width, found[0].height) == (
        800,
        600,
    )
    thumb = settings.data_dir / found[0].thumb_path
    assert thumb.exists()
    from PIL import Image

    assert max(Image.open(thumb).size) == 400
    assert found[1].thumb_path is None

    import_export(db, settings, path)
    assert len(photos(db)) == 2


def test_api_primary_photo_downloaded_once_and_deduplicated(db, settings, tmp_path, strava):
    primary = {"unique_id": "a", "urls": {"600": "https://cdn.test/a-600.jpg"}}
    strava.add(summary(1, total_photo_count=1, photos={"primary": primary, "count": 1}))
    strava.add(summary(2, day=1, total_photo_count=1,
                       photos={"primary": {**primary, "unique_id": "z"}, "count": 1}))  # fmt: skip

    sync(db, strava, settings=settings)

    found = {(p.unique_id, p.source) for p in photos(db)}
    assert found == {("a", "strava"), ("z", "strava")}
    assert (settings.data_dir / "photos/2/z.jpg").exists()

    import_export(db, settings, make_export(tmp_path / "export_1.zip"))
    by_ride = {}
    for p in photos(db):
        by_ride.setdefault(p.unique_id, p.source)
    assert by_ride["a"] == "export" and by_ride["z"] == "strava"


def test_new_export_keeps_curated_data(db, settings, tmp_path):
    from velotheque import companions
    from velotheque.tables import activity_companion

    mention = "[strava://athletes/77/4]Anna and [strava://athletes/88/3]Bob"
    import_export(db, settings, make_export(tmp_path / "export_1.zip", description=mention))
    with db.begin() as conn:
        ids = dict(conn.execute(select(activity.c.source_id, activity.c.id)).all())
        ride = ids[1]
        names = dict(conn.execute(select(companion.c.name, companion.c.id)).all())
        companions.remove(conn, ride, names["Anna"])
        companions.add(conn, ride, "Carla")
        before_photos = photos(db)

    renamed = [(1, *ROWS[0][1:2], "Forclaz, renamed", *ROWS[0][3:]), *ROWS[1:]]
    report = import_export(
        db, settings, make_export(tmp_path / "export_2.zip", rows=renamed, description=mention)
    )

    assert report.new_or_changed == 1 and report.files == 1
    second = settings.data_dir / "strava-export/export_2/activities"
    assert sorted(f.name for f in second.iterdir()) == ["101.gpx"]
    with db.connect() as conn:
        assert dict(conn.execute(select(activity.c.source_id, activity.c.id)).all()) == ids
        links = {
            r.name: (r.source, r.hidden)
            for r in conn.execute(
                select(companion.c.name, activity_companion.c.source, activity_companion.c.hidden)
                .join(activity_companion)
                .where(activity_companion.c.activity_id == ride)
            )
        }
    assert links == {
        "Anna": ("mention", True),
        "Bob": ("mention", False),
        "Carla": ("manual", False),
    }
    assert rides(db)[1].name == "Forclaz, renamed"
    assert [(p.activity_id, p.unique_id) for p in photos(db)] == [
        (p.activity_id, p.unique_id) for p in before_photos
    ]
