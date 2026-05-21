# COE Overview — Phase 4: Ingest Pipeline Orchestrator

**Goal:** Compose Phase 2 source clients + Phase 3 normalization + Postgres
writes into a single end-to-end pipeline. Delta logic from
`last_successful_run_at`, idempotent upserts into `coe_events` AND the
per-source `*_raw` audit tables, per-source failure isolation, bootstrap
handling, and a `coe_runs` audit row written every run.

**Architecture:** One `run()` entrypoint. Loads the owner resolver,
computes `since`, then `asyncio.gather(..., return_exceptions=True)`
across the sources. Each source coroutine opens **its own**
`AsyncSession` from a shared `async_sessionmaker` (SQLAlchemy
`AsyncSession` is not safe for concurrent use, so one-per-task is
mandatory). Per-source log events are captured into a
`ContextVar[list[dict]]`-backed structlog processor — NOT
`capture_logs()`, which patches global state and would leak across
gathered coroutines.

**Tech Stack:** SQLAlchemy 2.0 async + asyncpg, `asyncio.gather`,
`testcontainers[postgres]` for integration tests, structlog with a
custom contextvar processor.

**Scope:** Phase 4 of 7. Functionality. No external HTTP from this code
beyond what Phase 2 clients already do.

**Codebase verified:** 2026-05-21 — Phases 1–3 have landed.
`coe/db/models.py` exposes `CoeEvent` (with `UniqueConstraint("source",
"source_id")` declared directly in Phase 1), `CoeRun`, `Employee` (ORM),
`Source`, and the four per-source raw models (`JiraRaw`, `WizRaw`,
`CrowdstrikeRaw`, `VibraniumRaw`). `coe/ingest/` has the five source
clients. `coe/normalize.py` has the four `*_to_coe_event` functions.
`coe/owner_resolver.py` has `OwnerResolver` and `load_resolver`.
`coe/pipeline.py` does not exist.

---

## Acceptance Criteria Coverage

This phase implements and tests:

### coe-overview.AC3: Ingest pipeline
- **coe-overview.AC3.1 Success:** The pipeline reads the latest `last_successful_run_at`
  from `coe_runs` and uses it as the `since` parameter for each source
  client.
- **coe-overview.AC3.2 Success:** Running the pipeline twice in succession with no
  source-system changes between runs produces no diffs in `coe_events`
  business fields (upserts are idempotent). The `last_seen_at` metadata
  column IS updated on every run and is exempt from the no-diff guarantee.
- **coe-overview.AC3.3 Success:** A successful run writes a new `coe_runs` row with
  `status='ok'`, `finished_at` set, and `events_ingested` populated with
  the correct count.
- **coe-overview.AC3.4 Failure:** A failure from a single source produces a non-fatal
  entry in `coe_runs.errors_json` for that source and does not block the
  other sources from completing in the same run.
- **coe-overview.AC3.5 Bootstrap:** The first-ever run (no prior `coe_runs` row) uses a
  configured synthetic baseline timestamp, sets `is_bootstrap=true` on the
  `coe_runs` row, and ingests the current open backlog.

---

## External dependency findings (2026)

- **SQLAlchemy 2.0 PG upserts** use `from sqlalchemy.dialects.postgresql
  import insert` + `.on_conflict_do_update(index_elements=[...],
  set_=dict(...))`. Phase 1 already declares the
  `uq_coe_events_source_sourceid` constraint, so `index_elements` can
  reference the natural key columns directly.
- **`AsyncSession` concurrency:** SQLAlchemy documents that
  `AsyncSession` is NOT safe for concurrent use. Each `asyncio.gather`
  task must get its own session from a shared `async_sessionmaker`.
- **`asyncio.gather(*, return_exceptions=True)`** preserves task order
  in the result list — zip with the original source list to attribute
  errors.
- **structlog contextvars:** Use
  `structlog.contextvars.bind_contextvars(source=source.value)` inside
  each source coroutine + a custom processor that appends every event
  to a `ContextVar[list[dict]]`-bound list. This avoids the global-state
  problem of `capture_logs()` and gives per-task log capture safely
  across `asyncio.gather`.
- **testcontainers-python**: `PostgresContainer("postgres:16")`.
  Connection URL has `psycopg2` driver by default — replace with
  `asyncpg` before passing to `create_async_engine`.

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->

<!-- START_TASK_1 -->
### Task 1: Upsert helpers (`coe/db/upsert.py`)

**Verifies:** AC3.2 (idempotent `coe_events` upserts). Also writes the
per-source raw audit rows (supports the design's audit story).

**Files:**
- Create: `/Users/dmar/src/COE-overview/coe/db/upsert.py`
- Test: `/Users/dmar/src/COE-overview/tests/db/test_upsert.py` (integration)

**Implementation:**

Expose three helpers:

```python
async def upsert_coe_events(
    session: AsyncSession, events: Iterable[CoeEvent]
) -> int

async def insert_raw_records(
    session: AsyncSession, source: Source, records: Iterable[Mapping[str, Any]]
) -> int

_RAW_MODEL: dict[Source, type[Base]] = {
    Source.JIRA: JiraRaw,
    Source.WIZ: WizRaw,
    Source.CROWDSTRIKE: CrowdstrikeRaw,
    Source.VIBRANIUM: VibraniumRaw,
}
```

`upsert_coe_events`:
- Builds `INSERT ... ON CONFLICT (source, source_id) DO UPDATE SET ...`
  using `from sqlalchemy.dialects.postgresql import insert`. Phase 1
  declared the unique constraint, so the natural key is available.
- The `set_` dict updates every mutable column from `excluded.*`:
  `title`, `severity`, `status`, `owner_email`, `manager_email`,
  `missing_owner_in_hr`, `sla_due_at`, `priority`, `updated_at`,
  `last_seen_at`, `raw`. Excluded from the update: `id`, `source`,
  `source_id`, `opened_at`, `coe_review_status`.
- Accepts an iterable; batches up to N=500 per statement for memory.
- Returns the total number of rows processed.
- Caller commits the session.

`insert_raw_records`:
- Picks the right raw model from `_RAW_MODEL[source]`.
- Inserts each record as `(source_id, fetched_at=now(), payload=raw_dict)`.
  Uses `INSERT ... ON CONFLICT (source_id, fetched_at) DO NOTHING` so
  back-to-back runs don't error if a clock tick collides.
- Returns the number of rows inserted (after conflict skip).

**Testing:**
Integration tests against a testcontainer Postgres:
- AC3.2: Upserting the same 3 `CoeEvent` instances twice results in 3
  rows total (not 6). The second upsert updates `last_seen_at` to a
  newer value.
- AC3.2: Modifying `title` on one event and re-upserting updates that
  row's title only.
- `opened_at` is preserved across upserts (never updated).
- `insert_raw_records(Source.JIRA, [...])` writes to `jira_raw`; the
  same source-id inserted twice in the same second doesn't error
  (ON CONFLICT DO NOTHING).

**Verification:**
```bash
uv run pytest tests/db/test_upsert.py -v -m integration
```

**Commit:** `feat(db): idempotent upsert helpers (coe_events + raw tables)`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Run-tracking helpers (`coe/db/runs.py`)

**Verifies:** AC3.1 (read `last_successful_run_at`), AC3.3 (write run row),
AC3.5 (bootstrap detection).

**Files:**
- Create: `/Users/dmar/src/COE-overview/coe/db/runs.py`
- Test: `/Users/dmar/src/COE-overview/tests/db/test_runs.py` (integration)
- Modify: `/Users/dmar/src/COE-overview/coe/config.py` (add
  `bootstrap_lookback_days: int = 90`)

**Implementation:**

Expose four functions (one is a helper reused by Phase 5):

```python
@dataclass(frozen=True)
class RunContext:
    since: datetime          # the timestamp to use for delta queries
    is_bootstrap: bool       # True iff no prior run exists
    run_id: int              # primary key of the freshly-created coe_runs row


async def find_prior_run_finished_at(session: AsyncSession) -> datetime | None:
    """Returns the finished_at of the most recent ok/partial run, or None."""
    stmt = (
        select(CoeRun.finished_at)
        .where(CoeRun.status.in_(["ok", "partial"]))
        .order_by(CoeRun.finished_at.desc())
        .limit(1)
    )
    return await session.scalar(stmt)


async def start_run(session: AsyncSession, settings: Settings) -> RunContext: ...

async def finish_run(
    session: AsyncSession,
    run_id: int,
    *,
    status: str,           # "ok" | "partial" | "failed"
    events_ingested: int,
    errors_json: dict | None,
) -> None: ...
```

`start_run`:
1. `prior = await find_prior_run_finished_at(session)`.
2. If `prior` is not None → `is_bootstrap=False`, `since=prior`.
3. If None → `is_bootstrap=True`, `since=now(UTC) - timedelta(days=settings.bootstrap_lookback_days)`.
4. Inserts a new `coe_runs` row with `started_at=now()`, `status='running'`,
   `is_bootstrap=<computed>`, and **`since=<computed>`** — persisting the
   effective delta window on the row itself so the doc renderer (Phase 5)
   reads it directly rather than re-deriving it after the run finishes.
   Commits, and returns the row's PK.

**Why persist `since` on the row:** `find_prior_run_finished_at` returns
the most-recent ok/partial run's `finished_at`. By the time the doc
renderer calls it, the run we want a doc FOR is itself ok/partial — so
the helper would return the current run's timestamp, not the prior one.
Persisting `since` at `start_run` time locks in the correct window for
the row's lifetime.

`finish_run`:
- Updates the row: `finished_at=now()`, `status`, `events_ingested`,
  `errors_json`. Commits.

**Note:** `find_prior_run_finished_at` is used only by `start_run` in
this module. Phase 5's renderer reads `coe_runs.since` directly off the
row that `start_run` wrote (since the helper would return the current
run's own `finished_at` after the run completes).

**Testing:**
Integration tests:
- AC3.5: Empty `coe_runs` table → `start_run` returns
  `is_bootstrap=True` and `since == now() - 90 days` (±1 sec). A new
  `coe_runs` row exists with `status='running'`, `is_bootstrap=True`.
- AC3.1: With a prior `coe_runs` row finished at T1 with `status='ok'`,
  `start_run` returns `is_bootstrap=False` and `since == T1`.
- AC3.1: Mixed history — prior runs with `status='failed'` are ignored;
  the most-recent `ok`/`partial` finished_at is used.
- AC3.3: After `finish_run(run_id, status='ok', events_ingested=42,
  errors_json=None)`, the row has the expected values.
- `find_prior_run_finished_at` on an empty table returns `None`.

**Verification:**
```bash
uv run pytest tests/db/test_runs.py -v -m integration
```

**Commit:** `feat(db): start_run / finish_run + find_prior_run helper`
<!-- END_TASK_2 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 3-4) -->

<!-- START_TASK_3 -->
### Task 3: Pipeline orchestrator (`coe/pipeline.py`)

**Verifies:** AC3.1, AC3.2, AC3.3, AC3.4, AC3.5 — composes everything
above.

**Files:**
- Create: `/Users/dmar/src/COE-overview/coe/logging.py` (context-var
  log capture helpers)
- Create: `/Users/dmar/src/COE-overview/coe/pipeline.py`
- Create: `/Users/dmar/src/COE-overview/coe/__main__.py`

**Implementation:**

**`coe/logging.py`** — per-task log capture via ContextVar:

```python
from __future__ import annotations

import contextvars
from typing import Any

import structlog

_capture_buffer: contextvars.ContextVar[list[dict[str, Any]] | None] = (
    contextvars.ContextVar("coe_log_capture", default=None)
)


def capture_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor that appends to the current task's capture buffer."""
    buf = _capture_buffer.get()
    if buf is not None:
        buf.append(dict(event_dict))
    return event_dict


class capture_logs_ctx:
    """Context manager: collect structlog events emitted within this task."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._token: contextvars.Token[list[dict[str, Any]] | None] | None = None

    def __enter__(self) -> list[dict[str, Any]]:
        self._token = _capture_buffer.set(self.events)
        return self.events

    def __exit__(self, *exc: Any) -> None:
        assert self._token is not None
        _capture_buffer.reset(self._token)


def configure_structlog() -> None:
    """Wire up structlog with the capture processor first."""
    structlog.configure(
        processors=[
            capture_processor,
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        cache_logger_on_first_use=False,  # capture must see fresh logger
    )
```

Because each `asyncio.gather` task runs in its own contextvars copy,
`_capture_buffer` set inside one task is invisible to siblings — exactly
the per-task isolation we need.

**`coe/pipeline.py`**:

```python
@dataclass(frozen=True)
class PipelineResult:
    run_id: int
    status: str               # "ok" | "partial" | "failed"
    events_ingested: int
    errors_json: dict | None
    is_bootstrap: bool


@dataclass(frozen=True)
class SourceResult:
    source: Source
    events_ingested: int
    error: str | None         # None on success
    log_events: list[dict]    # captured structlog records for this source


async def run(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> PipelineResult: ...
```

Each Phase 2 client resolves `settings` via `get_settings()` internally
(it's an `@lru_cache`-decorated function). The pipeline calls
`fetcher(since)` without passing settings — this is intentional and
matches the client signatures.

`run()` algorithm:

1. Open one session: `async with session_factory() as session: ctx = await start_run(session, settings)`. Close.
2. Open another session for HR: `async with session_factory() as session: ...`. Run HR sync sequentially before the gather so the resolver sees fresh employees. Wrap in try/except; on failure produce a `SourceResult` with the error, but don't block.
3. Open another session: `async with session_factory() as session: resolver = await load_resolver(session)`. Close.
4. Build four coroutines for the non-HR sources:
   `_run_source(session_factory, ctx.since, resolver, source)`.
5. `results = await asyncio.gather(*coros, return_exceptions=True)`.
6. Pair each result back to its source. If the result is an `Exception`,
   produce a `SourceResult(source, 0, str(exception), [])`. Otherwise the
   coroutine returns its own `SourceResult`.
7. Compute aggregate `events_ingested = sum(...)`. Aggregate
   `errors_json = {source.value: {"error": ..., "warnings": [...]}}`
   for any source with an error or non-empty `log_events`.
8. Compute `status`:
   - `'ok'` if all sources had no error.
   - `'partial'` if at least one succeeded and at least one errored.
   - `'failed'` if all errored.
9. Open another session: write the final state via
   `await finish_run(session, ctx.run_id, ...)`.
10. Return a `PipelineResult` carrying the same totals — the doc
    renderer (Phase 5) uses `run_id` and `status` to find the right
    record to render against.

`_run_source(session_factory, since, resolver, source)`:

```python
async def _run_source(
    session_factory: async_sessionmaker[AsyncSession],
    since: datetime,
    resolver: OwnerResolver,
    source: Source,
) -> SourceResult:
    log = structlog.get_logger(__name__)
    with capture_logs_ctx() as logs:
        structlog.contextvars.bind_contextvars(source=source.value)
        fetcher, normalizer = _DISPATCH[source]
        natural_key = _NATURAL_KEY_FIELD[source]
        events: list[CoeEvent] = []

        # One long-lived session for raw audit so we can flush per batch.
        async with session_factory() as raw_session:
            raw_buffer: list[dict] = []
            async for raw in fetcher(since):
                raw_dict = raw.model_dump()
                # 1. Persist raw FIRST, before normalization is attempted.
                #    If normalization throws on this record, the raw audit
                #    has already been captured.
                raw_buffer.append(
                    {"source_id": raw_dict[natural_key], "payload": raw_dict}
                )
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
                except Exception as exc:  # noqa: BLE001 — intentional broad catch
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
```

**Key correctness points:**
- Each `_run_source` opens its own session via `session_factory` —
  satisfies SQLAlchemy's no-concurrent-AsyncSession rule.
- `capture_logs_ctx()` uses ContextVar, so sibling gathered coroutines
  don't see each other's log events.
- Raw records are buffered in-memory and **flushed to Postgres every 50
  records (and once more at end-of-loop)**. Normalization happens
  per-record *after* the record has been appended to the buffer, so:
  - For records within a batch that has already flushed: raw audit is
    on disk; a subsequent normalization exception cannot lose it.
  - For records in the *current* unflushed batch (up to 49 at any
    moment): a normalization exception is caught, logged with
    `source_id`, and the loop continues — the next flush (at count 50
    or at end-of-loop) still commits all buffered raw records,
    including the one whose normalization just failed.
  - The only way to lose raw audit for records in the current batch is
    an *un*caught exception (process crash, OOM, network drop mid-loop)
    — the normalization try/except specifically prevents that path
    becoming the common case. If stronger guarantees are needed (e.g.
    one source's records must never be lost even on crash), drop the
    batch size to 1; the trade-off is ~50× more commits per run.

`_DISPATCH` maps each `Source` to its `(fetch_updated_since, *_to_coe_event)`
pair from Phase 2 + Phase 3. Also define an explicit per-source
natural-key map so the raw-record insertion in `_run_source` doesn't rely
on duck-typed key lookup:

```python
_NATURAL_KEY_FIELD: dict[Source, str] = {
    Source.JIRA: "key",
    Source.WIZ: "id",
    Source.CROWDSTRIKE: "id",
    Source.VIBRANIUM: "id",
}
```

Then in `_run_source`, replace
`r["id"] if "id" in r else r["key"]` with
`r[_NATURAL_KEY_FIELD[source]]`.

**`coe/__main__.py`**:

```python
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from coe.config import get_settings
from coe.logging import configure_structlog
from coe.pipeline import run

# Path used by the K8s CronJob to hand run_id from the ingest container
# to the render container via an emptyDir volume. Overridable via env so
# local dev doesn't need /var/run/coe to exist.
RUN_ID_PATH = Path(os.environ.get("COE_RUN_ID_PATH", "/var/run/coe/last_run_id"))


async def main() -> None:
    configure_structlog()
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    result = await run(SessionLocal, settings)
    print(f"run_id={result.run_id} status={result.status} "
          f"events={result.events_ingested}")
    await engine.dispose()

    # Hand off the run_id to the downstream render container.
    try:
        RUN_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUN_ID_PATH.write_text(str(result.run_id))
    except OSError as exc:
        # Non-fatal: local dev without /var/run/coe still works. The render
        # container only depends on this file in production K8s; if it's
        # missing it falls back to "latest ok/partial run".
        print(f"warning: could not write {RUN_ID_PATH}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
```

**Testing:**
See Task 4 for the integration tests covering AC3.1–AC3.5.

For this task, run the manual smoke:
```bash
docker compose up -d postgres
uv run alembic upgrade head
uv run python -m coe
```
Expected: a `coe_runs` row written; sources with no creds appear in
`errors_json`; sources with creds populate `coe_events`.

**Commit:** `feat(pipeline): orchestrator with per-task sessions + contextvar logs`
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Pipeline integration tests (`tests/test_pipeline.py`)

**Verifies:** AC3.1, AC3.2, AC3.3, AC3.4, AC3.5.

**Files:**
- Create: `/Users/dmar/src/COE-overview/tests/conftest.py` (testcontainer
  fixtures)
- Create: `/Users/dmar/src/COE-overview/tests/test_pipeline.py`

**Implementation:**

`conftest.py` provides session-scoped container + per-test session
factory:

```python
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from testcontainers.postgres import PostgresContainer
from alembic.config import Config
from alembic import command


@pytest.fixture(scope="session")
def pg_container():
    container = PostgresContainer("postgres:16")
    container.start()
    yield container
    container.stop()


@pytest.fixture(scope="session")
def db_url(pg_container) -> str:
    return pg_container.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations(db_url):
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


@pytest.fixture
async def session_factory(db_url):
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    # Truncate mutable tables between tests
    async with factory() as s:
        await s.execute(
            text("TRUNCATE coe_events, coe_runs, employees, "
                 "jira_raw, wiz_raw, crowdstrike_raw, vibranium_raw CASCADE")
        )
        await s.commit()
    await engine.dispose()


@pytest.fixture
async def session(session_factory) -> AsyncSession:
    async with session_factory() as s:
        yield s
```

`test_pipeline.py` uses `respx` to mock all source HTTP calls and the
`session_factory` fixture for the pipeline:

- AC3.5: Empty `coe_runs` → `run(session_factory, settings)` produces a
  `PipelineResult` with `is_bootstrap=True`, `status='ok'`,
  `events_ingested` matching the mocked source records. `since` passed
  to mocked source clients is ≈ `now - 90 days`.
- AC3.1: With a prior `coe_runs` row at T1, `run()` produces a result
  whose source clients were called with `since=T1`.
- AC3.2: Running `run()` twice with the same mocked responses produces
  the same number of rows in `coe_events` after the second run.
- AC3.3: A successful single-source-only mock returns
  `status='ok'`, the matching `coe_runs` row has `finished_at` set and
  `events_ingested` correct.
- AC3.4: Configure respx to make Wiz return 401. Other four sources
  succeed. Result: `status='partial'`, `errors_json` contains a `"wiz"`
  key with the auth error message; other sources have their counts in
  `events_ingested`. Verify `jira_raw` / `crowdstrike_raw` /
  `vibranium_raw` tables were populated (Wiz's was not).
- AC3.4: Configure respx to make Wiz raise repeated 503. Result:
  `errors_json["wiz"]` contains a `TransientError` message; other
  sources still ingest.

**Verification:**
```bash
uv run pytest tests/test_pipeline.py -v -m integration
```

**Commit:** `test(pipeline): integration tests covering AC3.1-AC3.5`
<!-- END_TASK_4 -->

<!-- END_SUBCOMPONENT_B -->

---

## Done When

- [ ] `coe/db/upsert.py` exposes `upsert_coe_events` and
  `insert_raw_records`.
- [ ] `coe/db/runs.py` exposes `find_prior_run_finished_at`, `start_run`,
  `finish_run`.
- [ ] `coe/logging.py` exposes `capture_logs_ctx`, `capture_processor`,
  `configure_structlog`.
- [ ] `coe/pipeline.py` exposes `PipelineResult`, `SourceResult`, `run`.
- [ ] `coe/__main__.py` runs end-to-end.
- [ ] `tests/conftest.py` provides session-scoped testcontainer + per-test
  session factory.
- [ ] All Phase 4 tests pass.
- [ ] `uv run python -m coe` runs end-to-end against the local
  docker-compose Postgres (with at least one source credentialed).
- [ ] `uv run ruff check . && uv run mypy coe tests && uv run pytest`
  exit 0.
- [ ] Branch pushed.

## Notes for Subsequent Phases

- Phase 5's Google Doc renderer reads from `coe_events` and `coe_runs`
  to build the weekly doc. It does NOT call into the pipeline. It reads
  the `since` value directly off `coe_runs.since` (written here by
  `start_run`) — it does NOT re-derive `since` via
  `find_prior_run_finished_at`, which would return the current run's own
  finished_at after the run completes.
- Phase 7's K8s CronJob runs `alembic upgrade head && python -m coe &&
  python -m coe.doc` — see Phase 7 Task 3 for the manifest shape.
