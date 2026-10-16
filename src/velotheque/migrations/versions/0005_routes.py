"""routes: raw GPX files, routes with tags, merged stage files, overlap and coverage

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR

from velotheque.tables import ROUTE_SEARCH_TSV

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)


def _multiline():
    return Geometry("MULTILINESTRING", srid=4326, spatial_index=False)


def upgrade() -> None:
    op.create_table(
        "raw_gpx_file",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("sha256", sa.LargeBinary, nullable=False, unique=True),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("fetched_at", TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "route",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False, server_default="file"),
        sa.Column("notes", sa.Text),
        sa.Column("tags", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("geometry", _multiline()),
        sa.Column("distance_m", sa.Double, nullable=False, server_default=sa.text("0")),
        sa.Column("elevation_gain_m", sa.Double),
        sa.Column("search_tsv", TSVECTOR, sa.Computed(ROUTE_SEARCH_TSV, persisted=True)),
        sa.CheckConstraint("source IN ('strava', 'gpx_studio', 'file')", name="source"),
    )
    op.create_index("ix_route_geometry", "route", ["geometry"], postgresql_using="gist")
    op.create_index("ix_route_search_tsv", "route", ["search_tsv"], postgresql_using="gin")
    op.create_index("ix_route_tags", "route", ["tags"], postgresql_using="gin")
    op.create_table(
        "route_file",
        sa.Column("route_id", sa.Integer, sa.ForeignKey("route.id", ondelete="CASCADE")),
        sa.Column("position", sa.SmallInteger),
        sa.Column(
            "raw_gpx_file_id", sa.BigInteger, sa.ForeignKey("raw_gpx_file.id"), nullable=False
        ),
        sa.PrimaryKeyConstraint("route_id", "position"),
        sa.UniqueConstraint("raw_gpx_file_id"),
    )
    op.create_table(
        "route_simplified",
        sa.Column("route_id", sa.Integer, sa.ForeignKey("route.id", ondelete="CASCADE")),
        sa.Column("level", sa.Text),
        sa.Column("geometry", _multiline(), nullable=False),
        sa.PrimaryKeyConstraint("route_id", "level"),
        sa.CheckConstraint("level IN ('overview', 'detail')", name="level"),
    )
    op.create_index(
        "ix_route_simplified_geometry", "route_simplified", ["geometry"], postgresql_using="gist"
    )
    op.create_table(
        "route_ride_overlap",
        sa.Column("route_id", sa.Integer, sa.ForeignKey("route.id", ondelete="CASCADE")),
        sa.Column("activity_id", sa.BigInteger, sa.ForeignKey("activity.id", ondelete="CASCADE")),
        sa.Column("overlap_m", sa.Double, nullable=False),
        sa.Column("overlap_fraction", sa.Double, nullable=False),
        sa.Column("covered", _multiline(), nullable=False),
        sa.PrimaryKeyConstraint("route_id", "activity_id"),
    )
    op.create_index(None, "route_ride_overlap", ["activity_id"])
    op.create_table(
        "route_coverage",
        sa.Column(
            "route_id", sa.Integer, sa.ForeignKey("route.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("length_m", sa.Double, nullable=False),
        sa.Column("covered_m", sa.Double, nullable=False),
        sa.Column("coverage_fraction", sa.Double, nullable=False),
        sa.Column("covered", _multiline()),
    )


def downgrade() -> None:
    for table in (
        "route_coverage",
        "route_ride_overlap",
        "route_simplified",
        "route_file",
        "route",
        "raw_gpx_file",
    ):
        op.drop_table(table)
