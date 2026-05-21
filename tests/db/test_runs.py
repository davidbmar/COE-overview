"""Tests for run-tracking helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coe.config import get_settings
from coe.db.models import CoeRun
from coe.db.runs import find_prior_run_finished_at, finish_run, start_run


@pytest.mark.integration
async def test_find_prior_run_finished_at_empty(db_session: AsyncSession) -> None:
    """Test find_prior_run_finished_at on empty table returns None."""
    result = await find_prior_run_finished_at(db_session)
    assert result is None


@pytest.mark.integration
async def test_start_run_bootstrap(db_session: AsyncSession) -> None:
    """AC3.5: Empty coe_runs -> start_run returns is_bootstrap=True with lookback window."""
    settings = get_settings()

    result = await start_run(db_session, settings)

    # Check bootstrap flag
    assert result.is_bootstrap is True

    # Check that since is approximately now - 90 days
    expected_since = datetime.now(UTC) - timedelta(days=settings.bootstrap_lookback_days)
    time_diff = abs((result.since - expected_since).total_seconds())
    assert time_diff < 2  # Within 2 seconds

    # Check that a coe_runs row was created
    rows = await db_session.execute(select(CoeRun))
    all_rows = list(rows.scalars())
    assert len(all_rows) == 1
    assert all_rows[0].status == "running"
    assert all_rows[0].is_bootstrap is True


@pytest.mark.integration
async def test_start_run_with_prior_ok(db_session: AsyncSession) -> None:
    """AC3.1: With prior ok run, start_run returns is_bootstrap=False, since=prior_finished_at."""
    settings = get_settings()

    # Insert a prior run that finished at T1
    t1 = datetime.now(UTC) - timedelta(hours=1)
    prior_run = CoeRun(
        since=t1,
        status="ok",
        is_bootstrap=True,
        started_at=t1,
        finished_at=t1,
        events_ingested=10,
    )
    db_session.add(prior_run)
    await db_session.commit()

    # Start a new run
    result = await start_run(db_session, settings)

    # Should NOT be bootstrap; since should equal prior finished_at
    assert result.is_bootstrap is False
    assert result.since == t1


@pytest.mark.integration
async def test_start_run_mixed_history(db_session: AsyncSession) -> None:
    """AC3.1: Mixed history - failed runs ignored, most recent ok/partial is used."""
    settings = get_settings()

    now = datetime.now(UTC)
    t0 = now - timedelta(hours=3)
    t1 = now - timedelta(hours=2)
    t2 = now - timedelta(hours=1)

    # Insert three runs: failed at T0, ok at T1, partial at T2
    run_failed = CoeRun(
        since=t0,
        status="failed",
        is_bootstrap=True,
        started_at=t0,
        finished_at=t0,
        events_ingested=0,
    )
    run_ok = CoeRun(
        since=t1,
        status="ok",
        is_bootstrap=False,
        started_at=t1,
        finished_at=t1,
        events_ingested=5,
    )
    run_partial = CoeRun(
        since=t2,
        status="partial",
        is_bootstrap=False,
        started_at=t2,
        finished_at=t2,
        events_ingested=3,
    )
    db_session.add(run_failed)
    db_session.add(run_ok)
    db_session.add(run_partial)
    await db_session.commit()

    # Start a new run
    result = await start_run(db_session, settings)

    # Should use T2 (most recent ok/partial, not the failed run at T0)
    assert result.since == t2


@pytest.mark.integration
async def test_finish_run(db_session: AsyncSession) -> None:
    """AC3.3: finish_run sets finished_at, status, events_ingested, errors_json."""
    settings = get_settings()

    # Start a run
    ctx = await start_run(db_session, settings)
    await db_session.commit()

    # Finish the run
    await finish_run(
        db_session,
        ctx.run_id,
        status="ok",
        events_ingested=42,
        errors_json=None,
    )
    await db_session.commit()

    # Verify the row was updated
    rows = await db_session.execute(select(CoeRun).where(CoeRun.id == ctx.run_id))
    row = next(iter(rows.scalars()))

    assert row.status == "ok"
    assert row.events_ingested == 42
    assert row.errors_json is None
    assert row.finished_at is not None
