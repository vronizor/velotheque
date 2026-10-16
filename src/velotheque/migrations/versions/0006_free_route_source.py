"""route.source becomes free text; built-in values get their display spelling

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("source", "route", type_="check")
    op.execute(
        "UPDATE route SET source = CASE source WHEN 'strava' THEN 'Strava' "
        "WHEN 'gpx_studio' THEN 'gpx.studio' ELSE source END"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE route SET source = CASE source WHEN 'Strava' THEN 'strava' "
        "WHEN 'gpx.studio' THEN 'gpx_studio' ELSE 'file' END"
    )
    op.create_check_constraint("source", "route", "source IN ('strava', 'gpx_studio', 'file')")
