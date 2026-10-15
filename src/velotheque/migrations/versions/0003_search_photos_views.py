"""search (unaccent + pg_trgm, tsvector), activity photos, saved views

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

from velotheque.tables import SEARCH_TSV

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # unaccent() is only STABLE; pinning the dictionary makes it safe to declare IMMUTABLE,
    # which generated columns and indexes require.
    op.execute(
        """
        CREATE FUNCTION vt_unaccent(text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        RETURN public.unaccent('public.unaccent'::regdictionary, $1)
        """
    )
    op.add_column(
        "activity",
        sa.Column("search_tsv", TSVECTOR, sa.Computed(SEARCH_TSV, persisted=True)),
    )
    op.create_index("ix_activity_search_tsv", "activity", ["search_tsv"], postgresql_using="gin")

    op.create_table(
        "activity_photo",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "activity_id",
            sa.BigInteger,
            sa.ForeignKey("activity.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("unique_id", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("thumb_path", sa.Text),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("position", sa.SmallInteger, nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("activity_id", "unique_id"),
        sa.CheckConstraint("source IN ('export', 'strava')", name="source"),
        sa.CheckConstraint("kind IN ('image', 'video')", name="kind"),
    )

    op.create_table(
        "saved_view",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("entity", sa.Text, nullable=False, server_default="rides"),
        sa.Column("query_string", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("entity", "name"),
    )


def downgrade() -> None:
    op.drop_table("saved_view")
    op.drop_table("activity_photo")
    op.drop_index("ix_activity_search_tsv", table_name="activity")
    op.drop_column("activity", "search_tsv")
    op.execute("DROP FUNCTION vt_unaccent(text)")
