"""components: components, bike assignments, service types and events

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)


def _component_id() -> sa.Column:
    return sa.Column(
        "component_id",
        sa.Integer,
        sa.ForeignKey("component.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "component",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False, server_default=""),
        sa.Column("brand", sa.Text),
        sa.Column("model", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column("retired_at", TZ),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "component_assignment",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        _component_id(),
        sa.Column("bike_id", sa.Integer, sa.ForeignKey("bike.id"), nullable=False),
        sa.Column("mounted_at", TZ, nullable=False),
        sa.Column("removed_at", TZ),
        sa.CheckConstraint("removed_at IS NULL OR removed_at > mounted_at", name="dates"),
    )
    op.create_index(None, "component_assignment", ["bike_id"])
    op.execute(
        "ALTER TABLE component_assignment ADD CONSTRAINT ex_component_assignment_one_bike "
        "EXCLUDE USING gist (component_id WITH =, tstzrange(mounted_at, removed_at, '[)') WITH &&)"
    )
    op.create_table(
        "service_type",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        _component_id(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("level", sa.SmallInteger, nullable=False, server_default=sa.text("1")),
        sa.Column("interval_value", sa.Double, nullable=False),
        sa.Column("interval_unit", sa.Text, nullable=False),
        sa.Column("rides", sa.Text, nullable=False, server_default="all"),
        sa.CheckConstraint("level >= 1", name="level"),
        sa.CheckConstraint("interval_value > 0", name="interval_value"),
        sa.CheckConstraint("interval_unit IN ('km', 'hours', 'days')", name="interval_unit"),
        sa.CheckConstraint("rides IN ('all', 'outdoor', 'indoor')", name="rides"),
        sa.UniqueConstraint("id", "component_id"),
    )
    op.create_table(
        "service_event",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        _component_id(),
        sa.Column("service_type_id", sa.Integer),
        sa.Column("performed_at", TZ, nullable=False),
        sa.Column("notes", sa.Text),
        sa.ForeignKeyConstraint(
            ["service_type_id", "component_id"],
            ["service_type.id", "service_type.component_id"],
            name="fk_service_event_service_type",
        ),
    )
    op.create_index(None, "service_event", ["component_id", "performed_at"])


def downgrade() -> None:
    op.drop_table("service_event")
    op.drop_table("service_type")
    op.drop_table("component_assignment")
    op.drop_table("component")
