"""Run-tracking helpers for pipeline orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from coe.config import Settings
from coe.db.models import CoeRun


@dataclass(frozen=True)
class RunContext:
    """Context for a pipeline run."""

    since: datetime
    """The timestamp to use for delta queries."""

    is_bootstrap: bool
    """True iff no prior run exists."""

    run_id: int
    """Primary key of the freshly-created coe_runs row."""


async def find_prior_run_finished_at(session: AsyncSession) -> datetime | None:
    """Return the finished_at of the most recent ok/partial run, or None.

    Args:
        session: AsyncSession for the database connection.

    Returns:
        The finished_at timestamp of the most recent ok/partial run, or None if no such run exists.
    """
    stmt = (
        select(CoeRun.finished_at)
        .where(CoeRun.status.in_(["ok", "partial"]))
        .order_by(CoeRun.finished_at.desc())
        .limit(1)
    )
    return await session.scalar(stmt)


async def start_run(session: AsyncSession, settings: Settings) -> RunContext:
    """Start a new pipeline run and return its context.

    Determines whether this is a bootstrap run (no prior runs) and computes the delta window.

    Args:
        session: AsyncSession for the database connection.
        settings: Settings object with bootstrap_lookback_days.

    Returns:
        RunContext with since, is_bootstrap, and run_id set.

    Commits the session.
    """
    # Find the most recent prior ok/partial run
    prior = await find_prior_run_finished_at(session)

    if prior is not None:
        # Not a bootstrap run; use the prior run's finished_at as the delta window
        is_bootstrap = False
        since = prior
    else:
        # Bootstrap run; compute the window as now - lookback days
        is_bootstrap = True
        since = datetime.now(UTC) - timedelta(days=settings.bootstrap_lookback_days)

    # Insert a new coe_runs row with status='running'
    # Note: started_at uses server_default, so we only set the fields we control
    new_run = CoeRun(
        since=since,
        status="running",
        is_bootstrap=is_bootstrap,
    )
    session.add(new_run)
    await session.flush()  # Flush to get the row's ID
    run_id = new_run.id
    await session.commit()

    return RunContext(since=since, is_bootstrap=is_bootstrap, run_id=run_id)


async def finish_run(
    session: AsyncSession,
    run_id: int,
    *,
    status: str,
    events_ingested: int,
    errors_json: dict[str, Any] | None,
) -> None:
    """Finish a pipeline run with final status and metrics.

    Args:
        session: AsyncSession for the database connection.
        run_id: The coe_runs.id to update.
        status: Status string ("ok", "partial", or "failed").
        events_ingested: Count of events successfully ingested.
        errors_json: Error details by source, or None if no errors.

    Commits the session.
    """
    # Update the row with finished_at, status, events_ingested, errors_json
    row = await session.get(CoeRun, run_id)
    if row is None:
        msg = f"CoeRun with id {run_id} not found"
        raise ValueError(msg)

    row.finished_at = func.now()
    row.status = status
    row.events_ingested = events_ingested
    row.errors_json = errors_json

    await session.commit()
