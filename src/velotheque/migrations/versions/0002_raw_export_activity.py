"""raw_export_activity: rows and track file references from the Strava bulk export

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_export_activity",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("source_id", sa.BigInteger, nullable=False),
        sa.Column("export_name", sa.Text, nullable=False),
        sa.Column("file_path", sa.Text),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("payload_sha256", sa.LargeBinary, nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(None, "raw_export_activity", ["source_id", "id"])


def downgrade() -> None:
    op.drop_table("raw_export_activity")
