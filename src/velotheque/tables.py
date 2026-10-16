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
    ForeignKeyConstraint,
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, ExcludeConstraint

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
# geoalchemy2 copies a column's nullability onto its type, so nullable columns need their own.
OPTIONAL_MULTILINE = Geometry("MULTILINESTRING", srid=4326, spatial_index=False, nullable=True)
MULTIPOLYGON = Geometry("MULTIPOLYGON", srid=4326, spatial_index=False)
ANY_GEOMETRY = Geometry("GEOMETRY", srid=4326, spatial_index=False)
SEARCH_TSV = (
    "to_tsvector('simple'::regconfig, "
    "vt_unaccent(coalesce(name, '') || ' ' || coalesce(description, '')))"
)
ROUTE_SEARCH_TSV = (
    "to_tsvector('simple'::regconfig, "
    "vt_unaccent(coalesce(name, '') || ' ' || coalesce(notes, '')))"
)
# Sources are free text; these are offered as suggestions.
ROUTE_SOURCES = ("Strava", "gpx.studio", "file")
ROUTE_SOURCE_MAX = 40


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

raw_gpx_file = Table(
    "raw_gpx_file",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("sha256", LargeBinary, nullable=False, unique=True),
    Column("filename", Text, nullable=False),
    Column("content", LargeBinary, nullable=False),
    _now(),
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
    # Strava's group size, the owner included; it doesn't say who the others were.
    Column("athlete_count", SmallInteger),
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

# Routes: name, source, notes, tags and the route_file list are curated; geometry, distance
# and elevation are normalized from the raw files (`normalize` recomputes them).

route = Table(
    "route",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("name", Text, nullable=False),
    Column("source", Text, nullable=False, server_default="file"),
    Column("notes", Text),
    Column("tags", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    Column("created_at", TZ, nullable=False, server_default=func.now()),
    Column("geometry", OPTIONAL_MULTILINE),
    Column("distance_m", Double, nullable=False, server_default=text("0")),
    Column("elevation_gain_m", Double),
    # One value per vertex of geometry, in order (null where the file had no <ele>).
    Column("altitude_m", ARRAY(REAL)),
    Column("search_tsv", TSVECTOR, Computed(ROUTE_SEARCH_TSV, persisted=True)),
    Index("ix_route_geometry", "geometry", postgresql_using="gist"),
    Index("ix_route_search_tsv", "search_tsv", postgresql_using="gin"),
    Index("ix_route_tags", "tags", postgresql_using="gin"),
)

route_file = Table(
    "route_file",
    metadata,
    Column("route_id", Integer, ForeignKey("route.id", ondelete="CASCADE")),
    Column("position", SmallInteger),
    Column("raw_gpx_file_id", BigInteger, ForeignKey("raw_gpx_file.id"), nullable=False),
    PrimaryKeyConstraint("route_id", "position"),
    UniqueConstraint("raw_gpx_file_id"),
)

# Reference: GADM boundaries, loaded per country by `velotheque regions load`. Re-downloadable,
# so the nightly dump keeps region_country (what to reload) but not the region rows.

region_country = Table(
    "region_country",
    metadata,
    Column("country", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("sha256", LargeBinary, nullable=False),
    Column("regions", Integer, nullable=False),
    Column("loaded_at", TZ, nullable=False, server_default=func.now()),
)

region = Table(
    "region",
    metadata,
    # The GADM GID ("ESP.10.3.4.1_1"): stable across reloads, so it is what URLs carry.
    Column("id", Text, primary_key=True),
    Column(
        "country",
        Text,
        ForeignKey("region_country.country", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("level", SmallInteger, nullable=False),
    Column("name", Text, nullable=False),
    Column("alt_names", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    Column("type_label", Text, nullable=False),
    Column("parent_id", Text, ForeignKey("region.id", ondelete="CASCADE")),
    # "|name|variant|…|", lower-cased and unaccented, for word-prefix search.
    Column("search_key", Text, nullable=False),
    Column("geometry", MULTIPOLYGON, nullable=False),
    Index(None, "country"),
    Index(None, "parent_id"),
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

route_simplified = Table(
    "route_simplified",
    metadata,
    Column("route_id", Integer, ForeignKey("route.id", ondelete="CASCADE")),
    Column("level", Text),
    Column("geometry", MULTILINE, nullable=False),
    PrimaryKeyConstraint("route_id", "level"),
    CheckConstraint("level IN ('overview', 'detail')", name="level"),
    Index("ix_route_simplified_geometry", "geometry", postgresql_using="gist"),
)

route_ride_overlap = Table(
    "route_ride_overlap",
    metadata,
    Column("route_id", Integer, ForeignKey("route.id", ondelete="CASCADE")),
    Column("activity_id", BigInteger, ForeignKey("activity.id", ondelete="CASCADE")),
    Column("overlap_m", Double, nullable=False),
    Column("overlap_fraction", Double, nullable=False),
    Column("covered", MULTILINE, nullable=False),
    PrimaryKeyConstraint("route_id", "activity_id"),
    Index(None, "activity_id"),
)

route_coverage = Table(
    "route_coverage",
    metadata,
    Column("route_id", Integer, ForeignKey("route.id", ondelete="CASCADE"), primary_key=True),
    Column("length_m", Double, nullable=False),
    Column("covered_m", Double, nullable=False),
    Column("coverage_fraction", Double, nullable=False),
    Column("covered", OPTIONAL_MULTILINE),
)

# The deepest regions cut into small pieces, so a track only meets nearby vertices.
region_part = Table(
    "region_part",
    metadata,
    Column("region_id", Text, ForeignKey("region.id", ondelete="CASCADE"), nullable=False),
    Column("geometry", ANY_GEOMETRY, nullable=False),
    Index(None, "region_id"),
    Index("ix_region_part_geometry", "geometry", postgresql_using="gist"),
)

activity_region = Table(
    "activity_region",
    metadata,
    Column("activity_id", BigInteger, ForeignKey("activity.id", ondelete="CASCADE")),
    Column("region_id", Text, ForeignKey("region.id", ondelete="CASCADE")),
    PrimaryKeyConstraint("activity_id", "region_id"),
    Index(None, "region_id"),
)

route_region = Table(
    "route_region",
    metadata,
    Column("route_id", Integer, ForeignKey("route.id", ondelete="CASCADE")),
    Column("region_id", Text, ForeignKey("region.id", ondelete="CASCADE")),
    PrimaryKeyConstraint("route_id", "region_id"),
    Index(None, "region_id"),
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

# People ridden with: added by hand, or from a Strava mention in the description (the
# mention's athlete id ties later mentions to the same person). Removing a mentioned person
# from a ride hides the link, so the next sync doesn't bring it back.
companion = Table(
    "companion",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("name", Text, nullable=False),
    Column(
        "strava_athlete_ids",
        ARRAY(BigInteger),
        nullable=False,
        server_default=text("'{}'::bigint[]"),
    ),
    Column("created_at", TZ, nullable=False, server_default=func.now()),
)

activity_companion = Table(
    "activity_companion",
    metadata,
    Column("activity_id", BigInteger, ForeignKey("activity.id", ondelete="CASCADE")),
    Column("companion_id", Integer, ForeignKey("companion.id", ondelete="CASCADE")),
    Column("source", Text, nullable=False),
    Column("hidden", Boolean, nullable=False, server_default=text("false")),
    PrimaryKeyConstraint("activity_id", "companion_id"),
    CheckConstraint("source IN ('mention', 'manual')", name="source"),
    Index(None, "companion_id"),
)

# Components and maintenance. A component is on at most one bike at a time; usage is computed
# from the rides on that bike while it was mounted (components.py), never stored.
INTERVAL_UNITS = ("km", "hours", "days")
# Which rides wear a component: indoor = trainer flag or VirtualRide.
RIDE_SCOPES = ("all", "outdoor", "indoor")

component = Table(
    "component",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("name", Text, nullable=False),
    Column("kind", Text, nullable=False, server_default=""),
    Column("brand", Text),
    Column("model", Text),
    Column("notes", Text),
    Column("retired_at", TZ),
    Column("created_at", TZ, nullable=False, server_default=func.now()),
)

component_assignment = Table(
    "component_assignment",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("component_id", Integer, ForeignKey("component.id", ondelete="CASCADE"), nullable=False),
    Column("bike_id", Integer, ForeignKey("bike.id"), nullable=False),
    Column("mounted_at", TZ, nullable=False),
    Column("removed_at", TZ),
    CheckConstraint("removed_at IS NULL OR removed_at > mounted_at", name="dates"),
    ExcludeConstraint(
        ("component_id", "="),
        (func.tstzrange(text("mounted_at"), text("removed_at"), "[)"), "&&"),
        name="ex_component_assignment_one_bike",
        using="gist",
    ),
    Index(None, "bike_id"),
)

service_type = Table(
    "service_type",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("component_id", Integer, ForeignKey("component.id", ondelete="CASCADE"), nullable=False),
    Column("name", Text, nullable=False),
    Column("level", SmallInteger, nullable=False, server_default=text("1")),
    Column("interval_value", Double, nullable=False),
    Column("interval_unit", Text, nullable=False),
    Column("rides", Text, nullable=False, server_default="all"),
    Column("or_days", Double),  # "200 h or 365 days, whichever comes first"
    Column("description", Text),
    CheckConstraint("level >= 1", name="level"),
    CheckConstraint("interval_value > 0", name="interval_value"),
    CheckConstraint("interval_unit IN ('km', 'hours', 'days')", name="interval_unit"),
    CheckConstraint("rides IN ('all', 'outdoor', 'indoor')", name="rides"),
    CheckConstraint("or_days IS NULL OR (or_days > 0 AND interval_unit <> 'days')", name="or_days"),
    UniqueConstraint("id", "component_id"),
)

# An event without a service type is a note: it resets nothing.
service_event = Table(
    "service_event",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("component_id", Integer, ForeignKey("component.id", ondelete="CASCADE"), nullable=False),
    Column("service_type_id", Integer),
    Column("performed_at", TZ, nullable=False),
    Column("notes", Text),
    ForeignKeyConstraint(
        ["service_type_id", "component_id"],
        ["service_type.id", "service_type.component_id"],
        name="fk_service_event_service_type",
    ),
    Index(None, "component_id", "performed_at"),
)

DERIVED_TABLES = (
    activity_track_simplified,
    route_simplified,
    route_ride_overlap,
    route_coverage,
    region_part,
    activity_region,
    route_region,
)
