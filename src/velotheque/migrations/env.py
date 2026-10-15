from alembic import context
from sqlalchemy import create_engine

from velotheque.config import get_settings
from velotheque.migrations import include_object
from velotheque.tables import metadata


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    url = context.config.get_main_option("sqlalchemy.url") or get_settings().database_url
    with create_engine(url).connect() as connection:
        _run(connection)


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
