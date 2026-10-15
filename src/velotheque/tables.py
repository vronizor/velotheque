from geoalchemy2 import Geometry
from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

TZ = DateTime(timezone=True)
MULTILINE = Geometry("MULTILINESTRING", srid=4326, spatial_index=False)
SEARCH_TSV = (
    "to_tsvector('simple'::regconfig, "
    "vt_unaccent(coalesce(name, '') || ' ' || coalesce(description, '')))"
)


def _now():
    return Column("fetched_at", TZ, nullable=False, server_default=func.now())


# Raw: append-only source payloads. A new row is written only when the payload differs
# from the latest one for the same key.

raw_strava_activity = Table(
    "raw_strava_activity",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("source_id", BigInteger, nullable=False),
    Column("kind", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("payload_sha256", LargeBinary, nullable=False),
    _now(),
    CheckConstraint("kind IN ('summary', 'detail')", name="kind"),
    Index(None, "source_id", "kind", "id"),
)

raw_strava_streams = Table(
    "raw_strava_streams",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("source_id", BigInteger, nullable=False),
    Column("http_status", SmallInteger, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("payload_sha256", LargeBinary, nullable=False),
    _now(),
    Index(None, "source_id", "id"),
)

raw_strava_athlete = Table(
    "raw_strava_athlete",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("source_id", BigInteger, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("payload_sha256", LargeBinary, nullable=False),
    _now(),
    Index(None, "source_id", "id"),
)

raw_strava_gear = Table(
    "raw_strava_gear",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("source_id", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("payload_sha256", LargeBinary, nullable=False),
    _now(),
    Index(None, "source_id", "id"),
)

raw_export_activity = Table(
    "raw_export_activity",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("source_id", BigInteger, nullable=False),
    Column("export_name", Text, nullable=False),
    Column("file_path", Text),
    Column("payload", JSONB, nullable=False),
    Column("payload_sha256", LargeBinary, nullable=False),
    _now(),
    Index(None, "source_id", "id"),
)

# Operational

strava_auth = Table(
    "strava_auth",
    metadata,
    Column("id", SmallInteger, primary_key=True, server_default=text("1")),
    Column("athlete_id", BigInteger, nullable=False),
    Column("access_token", Text, nullable=False),
    Column("refresh_token", Text, nullable=False),
    Column("expires_at", TZ, nullable=False),
    Column("scope", Text, nullable=False),
    Column("updated_at", TZ, nullable=False, server_default=func.now()),
    CheckConstraint("id = 1", name="single_row"),
)

sync_job = Table(
    "sync_job",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("kind", Text, nullable=False),
    Column("activity_source_id", BigInteger),
    Column("status", Text, nullable=False, server_default="running"),
    Column("started_at", TZ, nullable=False, server_default=func.now()),
    Column("finished_at", TZ),
    Column("counts", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("error", Text),
    Column("resumes_after", TZ),
    CheckConstraint("kind IN ('sync', 'refresh')", name="kind"),
    CheckConstraint(
        "status IN ('running', 'succeeded', 'failed', 'rate_limited', 'interrupted')",
        name="status",
    ),
    Index(
        "uq_sync_job_one_running",
        text("(true)"),
        unique=True,
        postgresql_where=text("status = 'running'"),
    ),
)

# Normalized

bike = Table(
    "bike",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("strava_gear_id", Text, unique=True),
    Column("name", Text, nullable=False),
    Column("retired", Boolean, nullable=False, server_default=text("false")),
)

activity = Table(
    "activity",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("source", Text, nullable=False, server_default="strava"),
    Column("source_id", BigInteger, nullable=False),
    Column("name", Text, nullable=False, server_default=""),
    Column("description", Text),
    Column("type", Text, nullable=False),
    Column("start_at", TZ, nullable=False),
    Column("start_at_local", DateTime(timezone=False), nullable=False),
    Column("timezone", Text),
    Column("distance_m", Double, nullable=False, server_default=text("0")),
    Column("moving_time_s", Integer, nullable=False, server_default=text("0")),
    Column("elapsed_time_s", Integer, nullable=False, server_default=text("0")),
    Column("elevation_gain_m", Double, nullable=False, server_default=text("0")),
    Column("bike_id", Integer, ForeignKey("bike.id", ondelete="SET NULL")),
    Column("trainer", Boolean, nullable=False, server_default=text("false")),
    Column("has_photos", Boolean, nullable=False, server_default=text("false")),
    Column("synced_at", TZ, nullable=False, server_default=func.now()),
    Column(
        "search_tsv",
        TSVECTOR,
        Computed(SEARCH_TSV, persisted=True),
    ),
    UniqueConstraint("source", "source_id"),
    Index(None, "start_at_local"),
    Index(None, "type"),
    Index(None, "bike_id"),
    Index("ix_activity_search_tsv", "search_tsv", postgresql_using="gin"),
)

activity_photo = Table(
    "activity_photo",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column(
        "activity_id", BigInteger, ForeignKey("activity.id", ondelete="CASCADE"), nullable=False
    ),
    Column("unique_id", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("file_path", Text, nullable=False),
    Column("thumb_path", Text),
    Column("width", Integer),
    Column("height", Integer),
    Column("position", SmallInteger, nullable=False, server_default=text("0")),
    UniqueConstraint("activity_id", "unique_id"),
    CheckConstraint("source IN ('export', 'strava')", name="source"),
    CheckConstraint("kind IN ('image', 'video')", name="kind"),
)

activity_track = Table(
    "activity_track",
    metadata,
    Column(
        "activity_id",
        BigInteger,
        ForeignKey("activity.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("geometry", MULTILINE, nullable=False),
    Column("track_source", Text, nullable=False),
    Column("time_s", ARRAY(Integer)),
    Column("distance_m", ARRAY(REAL)),
    Column("altitude_m", ARRAY(REAL)),
    Column("moving", ARRAY(Boolean)),
    CheckConstraint("track_source IN ('streams', 'polyline', 'export')", name="track_source"),
    Index("ix_activity_track_geometry", "geometry", postgresql_using="gist"),
)

# Derived: safe to truncate and rebuild

activity_track_simplified = Table(
    "activity_track_simplified",
    metadata,
    Column("activity_id", BigInteger, ForeignKey("activity.id", ondelete="CASCADE")),
    Column("level", Text),
    Column("geometry", MULTILINE, nullable=False),
    PrimaryKeyConstraint("activity_id", "level"),
    CheckConstraint("level IN ('overview', 'detail')", name="level"),
    Index("ix_activity_track_simplified_geometry", "geometry", postgresql_using="gist"),
)

backup_status = Table(
    "backup_status",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("finished_at", TZ, nullable=False, server_default=func.now()),
    Column("ok", Boolean, nullable=False),
    Column("error", Text),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Index(None, "ok", "finished_at"),
)

# Curated

saved_view = Table(
    "saved_view",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("name", Text, nullable=False),
    Column("entity", Text, nullable=False, server_default="rides"),
    Column("query_string", Text, nullable=False),
    Column("created_at", TZ, nullable=False, server_default=func.now()),
    UniqueConstraint("entity", "name"),
)

DERIVED_TABLES = (activity_track_simplified,)
