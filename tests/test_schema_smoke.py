"""Smoke test: confirms the schema migrates cleanly into a fresh Postgres.

This is an integration test — it requires docker-compose's postgres to be
running on localhost:5432 with the coe/coe/coe creds.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from coe.config import get_settings

pytestmark = pytest.mark.integration


async def test_schema_smoke() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            tables = (
                (
                    await conn.execute(
                        text(
                            "SELECT tablename FROM pg_tables "
                            "WHERE schemaname = 'public' ORDER BY tablename"
                        )
                    )
                )
                .scalars()
                .all()
            )
        expected = {
            "alembic_version",
            "coe_events",
            "coe_runs",
            "crowdstrike_raw",
            "employees",
            "jira_raw",
            "vibranium_raw",
            "wiz_raw",
        }
        assert expected.issubset(set(tables)), f"missing tables: {expected - set(tables)}"
    finally:
        await engine.dispose()
