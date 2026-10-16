"""service_details: an optional calendar limit on km/hours services ("200 h or 1 year")
and a free-text description (what to do, links)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("service_type", sa.Column("or_days", sa.Double(), nullable=True))
    op.add_column("service_type", sa.Column("description", sa.Text(), nullable=True))
    op.create_check_constraint(
        "or_days", "service_type", "or_days IS NULL OR (or_days > 0 AND interval_unit <> 'days')"
    )


def downgrade() -> None:
    op.drop_constraint("or_days", "service_type", type_="check")
    op.drop_column("service_type", "description")
    op.drop_column("service_type", "or_days")
