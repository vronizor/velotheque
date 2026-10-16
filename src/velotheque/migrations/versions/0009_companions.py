"""companions: Strava group size on rides, people ridden with (by hand or from mentions)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-16
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same rule as companions.MENTION, frozen here.
MENTION = re.compile(r"\[strava://athletes/(\d+)/(\d+)\]")

LATEST_DESCRIPTIONS = """
SELECT a.id, CASE
  WHEN EXISTS (SELECT 1 FROM raw_strava_activity r WHERE r.source_id = a.source_id
               AND r.kind = 'detail')
  THEN (SELECT r.payload->>'description' FROM raw_strava_activity r
        WHERE r.source_id = a.source_id AND r.kind = 'detail' ORDER BY r.id DESC LIMIT 1)
  ELSE (SELECT e.payload->>'Activity Description' FROM raw_export_activity e
        WHERE e.source_id = a.source_id ORDER BY e.id DESC LIMIT 1)
END AS description
FROM activity a
ORDER BY a.start_at
"""


def upgrade() -> None:
    op.add_column("activity", sa.Column("athlete_count", sa.SmallInteger))
    op.create_table(
        "companion",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "strava_athlete_ids",
            ARRAY(sa.BigInteger),
            nullable=False,
            server_default=sa.text("'{}'::bigint[]"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "activity_companion",
        sa.Column("activity_id", sa.BigInteger, sa.ForeignKey("activity.id", ondelete="CASCADE")),
        sa.Column("companion_id", sa.Integer, sa.ForeignKey("companion.id", ondelete="CASCADE")),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("hidden", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("activity_id", "companion_id"),
        sa.CheckConstraint("source IN ('mention', 'manual')", name="source"),
    )
    op.create_index(None, "activity_companion", ["companion_id"])

    op.execute(
        "UPDATE activity a SET athlete_count = (SELECT (r.payload->>'athlete_count')::smallint "
        "FROM raw_strava_activity r WHERE r.source_id = a.source_id ORDER BY r.id DESC LIMIT 1)"
    )
    conn = op.get_bind()
    people: dict[int, int] = {}
    for activity_id, description in conn.execute(sa.text(LATEST_DESCRIPTIONS)):
        for m in MENTION.finditer(description or ""):
            name = description[m.end() : m.end() + int(m.group(2))].strip()
            if not name:
                continue
            athlete = int(m.group(1))
            if athlete not in people:
                people[athlete] = conn.execute(
                    sa.text(
                        "INSERT INTO companion (name, strava_athlete_ids) "
                        "VALUES (:name, ARRAY[CAST(:athlete AS bigint)]) RETURNING id"
                    ),
                    {"name": name, "athlete": athlete},
                ).scalar_one()
            conn.execute(
                sa.text(
                    "INSERT INTO activity_companion (activity_id, companion_id, source) "
                    "VALUES (:a, :c, 'mention') ON CONFLICT DO NOTHING"
                ),
                {"a": activity_id, "c": people[athlete]},
            )


def downgrade() -> None:
    op.drop_table("activity_companion")
    op.drop_table("companion")
    op.drop_column("activity", "athlete_count")
