"""pytest configuration for live PostgreSQL integration tests."""
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text


@pytest.fixture(scope="session", autouse=True)
def verify_postgres_test_service():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        return
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("select version()"))
            assert connection.dialect.name == "postgresql"
    finally:
        engine.dispose()


@pytest.fixture
def postgres_engine():
    """Apply the real migration into an isolated disposable schema per test."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")

    schema = f"test_{uuid4().hex}"
    admin_engine = create_engine(url)
    try:
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        try:
            migration_config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            with engine.connect() as connection:
                migration_config.attributes["connection"] = connection
                command.upgrade(migration_config, "head")
            yield engine
        finally:
            engine.dispose()
            with admin_engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
    finally:
        admin_engine.dispose()
