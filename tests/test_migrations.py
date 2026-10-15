from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from velotheque.migrations import include_object
from velotheque.tables import metadata


def test_migrations_match_tables(engine):
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"include_object": include_object, "compare_type": True}
        )
        assert compare_metadata(ctx, metadata) == []
