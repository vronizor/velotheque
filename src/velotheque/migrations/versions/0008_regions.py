"""regions: GADM boundaries per country, ride/route tags; Strava mentions dropped from descriptions

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)


def _tag_table(name: str, key: str, key_type, target: str) -> None:
    op.create_table(
        name,
        sa.Column(key, key_type, sa.ForeignKey(f"{target}.id", ondelete="CASCADE")),
        sa.Column("region_id", sa.Text, sa.ForeignKey("region.id", ondelete="CASCADE")),
        sa.PrimaryKeyConstraint(key, "region_id"),
    )
    op.create_index(None, name, ["region_id"])


def upgrade() -> None:
    op.create_table(
        "region_country",
        sa.Column("country", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("sha256", sa.LargeBinary, nullable=False),
        sa.Column("regions", sa.Integer, nullable=False),
        sa.Column("loaded_at", TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "region",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "country",
            sa.Text,
            sa.ForeignKey("region_country.country", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("level", sa.SmallInteger, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "alt_names", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")
        ),
        sa.Column("type_label", sa.Text, nullable=False),
        sa.Column("parent_id", sa.Text, sa.ForeignKey("region.id", ondelete="CASCADE")),
        sa.Column("search_key", sa.Text, nullable=False),
        sa.Column(
            "geometry",
            Geometry("MULTIPOLYGON", srid=4326, spatial_index=False),
            nullable=False,
        ),
    )
    op.create_index(None, "region", ["country"])
    op.create_index(None, "region", ["parent_id"])
    op.create_table(
        "region_part",
        sa.Column(
            "region_id",
            sa.Text,
            sa.ForeignKey("region.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("geometry", Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=False),
    )
    op.create_index(None, "region_part", ["region_id"])
    op.create_index("ix_region_part_geometry", "region_part", ["geometry"], postgresql_using="gist")
    _tag_table("activity_region", "activity_id", sa.BigInteger, "activity")
    _tag_table("route_region", "route_id", sa.Integer, "route")
    # Same rule as normalize.MENTION: "[strava://athletes/<id>/<len>]Name" keeps only the name.
    op.execute(
        "UPDATE activity SET description = "
        r"regexp_replace(description, '\[strava://[^]\s]*\]', '', 'g') "
        "WHERE description LIKE '%[strava://%'"
    )


def downgrade() -> None:
    for table in ("route_region", "activity_region", "region_part", "region", "region_country"):
        op.drop_table(table)
