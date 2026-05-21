"""Pipeline orchestrator: compose ingest clients, normalization, and DB writes.

Implements a single end-to-end run that:
1. Loads the last successful run timestamp
2. Runs HR sync sequentially (non-fatal on failure)
3. Gathers the four source ingest tasks with per-task log capture and sessions
4. Writes results idempotently into coe_events and per-source raw audit tables
5. Records the run's final status and metrics into coe_runs
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coe.config import Settings
from coe.db.employees import upsert_employees
from coe.db.models import CoeEvent, Source
from coe.db.runs import finish_run, start_run
from coe.db.upsert import insert_raw_records, upsert_coe_events
from coe.ingest.crowdstrike import fetch_updated_since as crowdstrike_fetch
from coe.ingest.hr import fetch_all_active_employees
from coe.ingest.jira import fetch_updated_since as jira_fetch
from coe.ingest.vibranium import fetch_updated_since as vibranium_fetch
from coe.ingest.wiz import fetch_updated_since as wiz_fetch
from coe.logging import CaptureLogsCtx
from coe.normalize import (
    crowdstrike_to_coe_event,
    jira_to_coe_event,
    vibranium_to_coe_event,
    wiz_to_coe_event,
)
from coe.owner_resolver import OwnerResolver, load_resolver


@dataclass(frozen=True)
class PipelineResult:
    """Result of a complete pipeline run."""

    run_id: int
    """Primary key of the coe_runs row."""

    status: str
    """Run status: 'ok', 'partial', or 'failed'."""

    events_ingested: int
    """Total number of events successfully ingested."""

    errors_json: dict[str, Any] | None
    """Per-source error details and warnings, or None if no errors."""

    is_bootstrap: bool
    """True if this was the first-ever run."""


@dataclass(frozen=True)
class SourceResult:
    """Result of a single source's ingest."""

    source: Source
    """The source enum."""

    events_ingested: int
    """Number of events successfully normalized and queued."""

    error: str | None
    """None on success, error message on failure."""

    log_events: list[dict[str, Any]]
    """Captured structlog records from this source task."""


# Dispatch map: Source → (fetch_func, normalize_func) pair
_DISPATCH: dict[Source, tuple[Any, Any]] = {
    Source.JIRA: (jira_fetch, jira_to_coe_event),
    Source.WIZ: (wiz_fetch, wiz_to_coe_event),
    Source.CROWDSTRIKE: (crowdstrike_fetch, crowdstrike_to_coe_event),
    Source.VIBRANIUM: (vibranium_fetch, vibranium_to_coe_event),
}

# Per-source natural key field in raw payload
_NATURAL_KEY_FIELD: dict[Source, str] = {
    Source.JIRA: "key",
    Source.WIZ: "id",
    Source.CROWDSTRIKE: "id",
    Source.VIBRANIUM: "id",
}


async def _run_source(
    session_factory: async_sessionmaker[AsyncSession],
    since: datetime,
    resolver: OwnerResolver,
    source: Source,
) -> SourceResult:
    """Run a single source's ingest pipeline.

    Opens one session for raw audit writes (with periodic flushes every 50 records),
    normalizes each record after buffering its raw form, then upserts all
    successfully-normalized events in a final batch.

    Log events are captured into the task-local ContextVar buffer.

    Args:
        session_factory: async_sessionmaker for creating per-session connections.
        since: Timestamp for delta queries.
        resolver: OwnerResolver pre-loaded with active employees.
        source: The source to ingest from.

    Returns:
        SourceResult with ingestion metrics and any error encountered.
    """
    log = structlog.get_logger(__name__)
    with CaptureLogsCtx() as logs:
        structlog.contextvars.bind_contextvars(source=source.value)
        try:
            fetcher, normalizer = _DISPATCH[source]
            natural_key = _NATURAL_KEY_FIELD[source]
            events: list[CoeEvent] = []

            # One long-lived session for raw audit so we can flush per batch.
            async with session_factory() as raw_session:
                raw_buffer: list[dict[str, Any]] = []
                async for raw in fetcher(since):
                    raw_dict = raw.model_dump()
                    # 1. Persist raw FIRST, before normalization is attempted.
                    #    If normalization throws on this record, the raw audit
                    #    has already been captured.
                    raw_buffer.append({"source_id": raw_dict[natural_key], "payload": raw_dict})
                    if len(raw_buffer) >= 50:
                        await insert_raw_records(raw_session, source, raw_buffer)
                        await raw_session.commit()
                        raw_buffer.clear()

                    # 2. Normalize. Per-record failures log a warning but do
                    #    not abort the source — raw audit survives, and other
                    #    records in this source can still be processed.
                    try:
                        ev = normalizer(raw)
                        resolved = resolver.resolve(ev.owner_email)
                        ev.owner_email = resolved.owner_email
                        ev.manager_email = resolved.manager_email
                        ev.missing_owner_in_hr = resolved.missing_owner_in_hr
                        events.append(ev)
                    except Exception as exc:
                        log.warning(
                            "normalize-failed",
                            source_id=raw_dict.get(natural_key),
                            error=str(exc),
                        )

                # Flush any remaining raw records.
                if raw_buffer:
                    await insert_raw_records(raw_session, source, raw_buffer)
                    await raw_session.commit()

            # 3. Upsert all successfully-normalized events in one batch.
            async with session_factory() as session:
                ingested = await upsert_coe_events(session, events)
                await session.commit()

            structlog.contextvars.unbind_contextvars("source")
            return SourceResult(source, ingested, error=None, log_events=list(logs))

        except Exception as exc:
            structlog.contextvars.unbind_contextvars("source")
            return SourceResult(source, 0, error=str(exc), log_events=list(logs))


async def run(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> PipelineResult:
    """Execute the complete ingest pipeline.

    Algorithm:
    1. Open session, start_run to get context (since, is_bootstrap, run_id)
    2. Run HR sync sequentially (non-fatal on error)
    3. Load owner resolver from employees table
    4. asyncio.gather the four source tasks with return_exceptions=True
    5. Aggregate results: compute status, errors_json, events_ingested
    6. Write final state via finish_run
    7. Return PipelineResult with run_id and final metrics

    Args:
        session_factory: async_sessionmaker for creating per-session connections.
        settings: Settings with bootstrap_lookback_days, etc.

    Returns:
        PipelineResult with run_id, status, events_ingested, errors_json, is_bootstrap.
    """
    log = structlog.get_logger(__name__)

    # 1. Start run: determine since, is_bootstrap, and create coe_runs row
    async with session_factory() as session:
        ctx = await start_run(session, settings)

    # 2. HR sync: sequential, non-fatal on failure
    try:
        with CaptureLogsCtx():
            structlog.contextvars.bind_contextvars(source="hr")
            async with session_factory() as session:
                employees = []
                async for emp in fetch_all_active_employees(settings):
                    employees.append(emp)
                await upsert_employees(session, employees)
                await session.commit()
            structlog.contextvars.unbind_contextvars("source")
    except Exception:
        log.warning("hr-sync-failed", exc_info=True)
        structlog.contextvars.unbind_contextvars("source")

    # 3. Load owner resolver
    async with session_factory() as session:
        resolver = await load_resolver(session)

    # 4. Build and gather the four source coroutines
    sources = [Source.JIRA, Source.WIZ, Source.CROWDSTRIKE, Source.VIBRANIUM]
    coros = [_run_source(session_factory, ctx.since, resolver, source) for source in sources]
    results = await asyncio.gather(*coros, return_exceptions=True)

    # 5. Pair results back to sources and aggregate
    source_results: list[SourceResult] = []
    for source, result in zip(sources, results, strict=True):
        if isinstance(result, Exception):
            source_results.append(SourceResult(source, 0, error=str(result), log_events=[]))
        else:
            source_results.append(result)  # type: ignore[arg-type]

    # Aggregate metrics
    events_ingested = sum(sr.events_ingested for sr in source_results)

    # Build errors_json: per-source entry if error or non-empty log_events
    errors_json: dict[str, Any] | None = None
    has_error = False
    has_success = False

    for sr in source_results:
        if sr.error or sr.log_events:
            if errors_json is None:
                errors_json = {}
            errors_json[sr.source.value] = {
                "error": sr.error,
                "warnings": sr.log_events if sr.log_events else [],
            }
            if sr.error:
                has_error = True
            else:
                has_success = True
        else:
            has_success = True

    # Compute status: ok if no errors, partial if mixed, failed if all errors
    if not has_error:
        status = "ok"
    elif has_success:
        status = "partial"
    else:
        status = "failed"

    # 6. Write final state
    async with session_factory() as session:
        await finish_run(
            session,
            ctx.run_id,
            status=status,
            events_ingested=events_ingested,
            errors_json=errors_json,
        )

    # 7. Return result
    return PipelineResult(
        run_id=ctx.run_id,
        status=status,
        events_ingested=events_ingested,
        errors_json=errors_json,
        is_bootstrap=ctx.is_bootstrap,
    )
