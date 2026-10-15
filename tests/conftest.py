import os
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.fake_strava import FakeStrava
from velotheque.cli import alembic_config
from velotheque.config import get_settings
from velotheque.strava import oauth
from velotheque.tables import metadata

TEST_DB = "velotheque_test"


def _test_url() -> str:
    explicit = os.environ.get("VELOTHEQUE_TEST_DATABASE_URL")
    if explicit:
        return explicit
    return (
        make_url(get_settings().database_url)
        .set(database=TEST_DB)
        .render_as_string(hide_password=False)
    )


@pytest.fixture(scope="session")
def engine():
    url = make_url(_test_url())
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()
    command.upgrade(alembic_config(url.render_as_string(hide_password=False)), "head")
    eng = create_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    yield engine
    with engine.begin() as c:
        names = ", ".join(t.name for t in metadata.sorted_tables)
        c.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def conn(db):
    with db.connect() as connection:
        yield connection
        connection.rollback()


@pytest.fixture
def strava(db):
    with db.begin() as conn:
        oauth.save_tokens(
            conn,
            {
                "access_token": "a0",
                "refresh_token": "r0",
                "expires_at": int((datetime.now(UTC) + timedelta(hours=5)).timestamp()),
            },
            athlete_id=99,
            scope="read,activity:read_all,profile:read_all",
        )
    return FakeStrava()
