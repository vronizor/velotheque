"""initial schema: raw strava, auth, sync jobs, activities, tracks

Revision ID: 0001
Revises:
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)


def _multiline():
    return Geometry("MULTILINESTRING", srid=4326, spatial_index=False)


def _raw_table(name: str, source_id_type, *extra: sa.Column) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("source_id", source_id_type, nullable=False),
        *extra,
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("payload_sha256", sa.LargeBinary, nullable=False),
        sa.Column("fetched_at", TZ, nullable=False, server_default=sa.func.now()),
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    _raw_table("raw_strava_activity", sa.BigInteger, sa.Column("kind", sa.Text, nullable=False))
    op.create_check_constraint("kind", "raw_strava_activity", "kind IN ('summary', 'detail')")
    op.create_index(None, "raw_strava_activity", ["source_id", "kind", "id"])

    _raw_table(
        "raw_strava_streams",
        sa.BigInteger,
        sa.Column("http_status", sa.SmallInteger, nullable=False),
    )
    op.create_index(None, "raw_strava_streams", ["source_id", "id"])

    _raw_table("raw_strava_athlete", sa.BigInteger)
    op.create_index(None, "raw_strava_athlete", ["source_id", "id"])

    _raw_table("raw_strava_gear", sa.Text)
    op.create_index(None, "raw_strava_gear", ["source_id", "id"])

    op.create_table(
        "strava_auth",
        sa.Column("id", sa.SmallInteger, primary_key=True, server_default=sa.text("1")),
        sa.Column("athlete_id", sa.BigInteger, nullable=False),
        sa.Column("access_token", sa.Text, nullable=False),
        sa.Column("refresh_token", sa.Text, nullable=False),
        sa.Column("expires_at", TZ, nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("updated_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="single_row"),
    )

    op.create_table(
        "sync_job",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("activity_source_id", sa.BigInteger),
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column("started_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", TZ),
        sa.Column("counts", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text),
        sa.Column("resumes_after", TZ),
        sa.CheckConstraint("kind IN ('sync', 'refresh')", name="kind"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'rate_limited', 'interrupted')",
            name="status",
        ),
    )
    op.create_index(
        "uq_sync_job_one_running",
        "sync_job",
        [sa.text("(true)")],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )

    op.create_table(
        "bike",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("strava_gear_id", sa.Text, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("retired", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "activity",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("source", sa.Text, nullable=False, server_default="strava"),
        sa.Column("source_id", sa.BigInteger, nullable=False),
        sa.Column("name", sa.Text, nullable=False, server_default=""),
        sa.Column("description", sa.Text),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("start_at", TZ, nullable=False),
        sa.Column("start_at_local", sa.DateTime(timezone=False), nullable=False),
        sa.Column("timezone", sa.Text),
        sa.Column("distance_m", sa.Double, nullable=False, server_default=sa.text("0")),
        sa.Column("moving_time_s", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("elapsed_time_s", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("elevation_gain_m", sa.Double, nullable=False, server_default=sa.text("0")),
        sa.Column("bike_id", sa.Integer, sa.ForeignKey("bike.id", ondelete="SET NULL")),
        sa.Column("trainer", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("has_photos", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("synced_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source", "source_id"),
    )
    op.create_index(None, "activity", ["start_at_local"])
    op.create_index(None, "activity", ["type"])
    op.create_index(None, "activity", ["bike_id"])

    op.create_table(
        "activity_track",
        sa.Column(
            "activity_id",
            sa.BigInteger,
            sa.ForeignKey("activity.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("geometry", _multiline(), nullable=False),
        sa.Column("track_source", sa.Text, nullable=False),
        sa.Column("time_s", ARRAY(sa.Integer)),
        sa.Column("distance_m", ARRAY(sa.REAL)),
        sa.Column("altitude_m", ARRAY(sa.REAL)),
        sa.Column("moving", ARRAY(sa.Boolean)),
        sa.CheckConstraint(
            "track_source IN ('streams', 'polyline', 'export')", name="track_source"
        ),
    )
    op.create_index(
        "ix_activity_track_geometry", "activity_track", ["geometry"], postgresql_using="gist"
    )

    op.create_table(
        "activity_track_simplified",
        sa.Column("activity_id", sa.BigInteger, sa.ForeignKey("activity.id", ondelete="CASCADE")),
        sa.Column("level", sa.Text),
        sa.Column("geometry", _multiline(), nullable=False),
        sa.PrimaryKeyConstraint("activity_id", "level"),
        sa.CheckConstraint("level IN ('overview', 'detail')", name="level"),
    )
    op.create_index(
        "ix_activity_track_simplified_geometry",
        "activity_track_simplified",
        ["geometry"],
        postgresql_using="gist",
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'velotheque_ro') THEN
            GRANT USAGE ON SCHEMA public TO velotheque_ro;
            GRANT SELECT ON ALL TABLES IN SCHEMA public TO velotheque_ro;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO velotheque_ro;
            REVOKE SELECT ON strava_auth FROM velotheque_ro;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    for table in (
        "activity_track_simplified",
        "activity_track",
        "activity",
        "bike",
        "sync_job",
        "strava_auth",
        "raw_strava_gear",
        "raw_strava_athlete",
        "raw_strava_streams",
        "raw_strava_activity",
    ):
        op.drop_table(table)
