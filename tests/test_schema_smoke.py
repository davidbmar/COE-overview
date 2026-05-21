"""Smoke test: confirms the schema migrates cleanly into a fresh Postgres.

This is an integration test — it requires docker-compose's postgres to be
running on localhost:5432 with the coe/coe/coe creds, and alembic upgrade head
to have been run beforehand to set up the schema.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from coe.config import get_settings

pytestmark = pytest.mark.integration


async def test_schema_smoke() -> None:
    """Verify all expected tables exist after migration."""
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


async def test_schema_enums() -> None:
    """Verify enum types exist with correct labels (lowercase source_enum, uppercase coe_severity)."""
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            enums = (
                await conn.execute(
                    text(
                        "SELECT enumtypid, enumlabel FROM pg_enum ORDER BY enumtypid, enumsortorder"
                    )
                )
            ).all()
        enum_labels = [label for _, label in enums]

        # source_enum should have lowercase values
        assert "jira" in enum_labels, "source_enum missing 'jira' label"
        assert "wiz" in enum_labels, "source_enum missing 'wiz' label"
        assert "crowdstrike" in enum_labels, "source_enum missing 'crowdstrike' label"
        assert "vibranium" in enum_labels, "source_enum missing 'vibranium' label"

        # coe_severity should have uppercase values
        assert "CRITICAL" in enum_labels, "coe_severity missing 'CRITICAL' label"
        assert "HIGH" in enum_labels, "coe_severity missing 'HIGH' label"
        assert "UNKNOWN" in enum_labels, "coe_severity missing 'UNKNOWN' label"
    finally:
        await engine.dispose()


async def test_schema_constraints() -> None:
    """Verify expected unique constraint exists on coe_events."""
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            constraints = (
                (
                    await conn.execute(
                        text(
                            "SELECT constraint_name FROM information_schema.table_constraints "
                            "WHERE table_name = 'coe_events' AND constraint_type = 'UNIQUE'"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert "uq_coe_events_source_sourceid" in constraints, (
            "coe_events missing uq_coe_events_source_sourceid unique constraint"
        )
    finally:
        await engine.dispose()
