"""route.altitude_m: per-vertex altitudes for the route elevation chart

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("route", sa.Column("altitude_m", ARRAY(sa.REAL)))


def downgrade() -> None:
    op.drop_column("route", "altitude_m")
