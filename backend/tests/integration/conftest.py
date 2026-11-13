import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlmodel import Session

from app.config import load_settings


@pytest.fixture
def pg_engine():
    if os.getenv("A4J_RUN_PG_TESTS") != "1":
        pytest.skip("set A4J_RUN_PG_TESTS=1 for real PostgreSQL integration checks")
    settings = load_settings()
    admin = create_engine(
        settings.database_url.get_secret_value(),
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    schema = "a4j_test_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        settings.database_url.get_secret_value(),
        hide_parameters=True,
        connect_args={"options": f"-csearch_path={schema}", "connect_timeout": 5},
    )
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def db(pg_engine):
    with Session(pg_engine) as session:
        yield session
        session.rollback()
