# COE Overview — Phase 3: Normalization Layer

**Goal:** Convert raw per-source records (produced by Phase 2 clients)
into a unified `CoeEvent` shape with normalized severity, resolve
owner→manager via the local `employees` table, and flag rows with
unresolved owners.

**Architecture:** Pure functions, no I/O. `coe/normalize.py` exposes
per-source `to_coe_event(raw, source) -> CoeEvent` functions and a
shared severity mapping table. `coe/owner_resolver.py` is a thin layer
over a Postgres read of the `employees` table — but accepts a pre-loaded
dict for testability so its core is also pure.

**Tech Stack:** Python stdlib + `pydantic` (already pinned).

**Scope:** Phase 3 of 7. Functionality. No I/O in the normalizers
themselves; the owner resolver has one thin Postgres read function on top
of a pure resolver.

**Codebase verified:** 2026-05-21 — Phases 1 and 2 have landed.
`coe/db/models.py` exposes `CoeSeverity`, `Source`. `coe/ingest/` has the
typed source models (`JiraIssue`, `WizIssue`, `CrowdstrikeDetect`,
`VibraniumIncident`, `Employee`). `coe/normalize.py` does not exist.

---

## Acceptance Criteria Coverage

This phase implements and tests:

### coe-overview.AC2: Normalization
- **coe-overview.AC2.1 Success:** Each source's native severity value maps to the
  `coe_severity` enum (`CRITICAL` or `HIGH`) per a documented per-source
  mapping table.
- **coe-overview.AC2.2 Failure:** A severity value not in the mapping table falls back to
  `UNKNOWN`, and a warning is logged including the source and the unmapped
  raw value.
- **coe-overview.AC2.3 Success:** Given an `owner_email` from a source record, the owner
  resolver returns the matching `manager_email` from the local `employees`
  table.
- **coe-overview.AC2.4 Failure:** An `owner_email` not present in `employees` resolves to
  `None`, and the resulting `coe_events` row is flagged with
  `missing_owner_in_hr=true`.

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->

<!-- START_TASK_1 -->
### Task 1: Severity normalization + per-source `to_coe_event`

**Verifies:** AC2.1, AC2.2.

**Files:**
- Create: `/Users/dmar/src/COE-overview/coe/normalize.py`
- Test: `/Users/dmar/src/COE-overview/tests/test_normalize.py` (unit)

**Implementation:**

Define a `SEVERITY_MAP` constant — a dict-of-dicts keyed by `Source` then
by the source's native value:

```python
SEVERITY_MAP: dict[Source, dict[str, CoeSeverity]] = {
    Source.JIRA: {
        # Jira uses Priority not Severity; map our policy
        "Highest": CoeSeverity.CRITICAL,
        "High": CoeSeverity.HIGH,
    },
    Source.WIZ: {
        "CRITICAL": CoeSeverity.CRITICAL,
        "HIGH": CoeSeverity.HIGH,
    },
    Source.CROWDSTRIKE: {
        # Already pre-bucketed by the client into CRITICAL/HIGH
        "CRITICAL": CoeSeverity.CRITICAL,
        "HIGH": CoeSeverity.HIGH,
    },
    Source.VIBRANIUM: {
        # ⚠ BLOCKER FOR PHASE 3 MERGE: Vibranium severity values below
        # are placeholders. Before this phase is merged, the engineer
        # MUST pull the Vibranium API docs (see Phase 2 Task 6's risk
        # callout), capture the actual severity string values returned
        # by the API, and update this mapping. AC2.1 cannot be claimed
        # passing until this is real data.
        "CRITICAL": CoeSeverity.CRITICAL,
        "HIGH": CoeSeverity.HIGH,
    },
}
```

Expose:

```python
def normalize_severity(source: Source, raw_value: str) -> CoeSeverity
```

- Looks up `raw_value` (case-sensitive) in `SEVERITY_MAP[source]`.
- On hit: returns the mapped enum.
- On miss: emits a structlog `warning` with `source` and `raw_value`,
  returns `CoeSeverity.UNKNOWN`.

Expose four source-specific normalizers, each taking a source-specific
typed model from Phase 2 and returning an unsaved
`coe.db.models.CoeEvent` (no `id`, no `last_seen_at`):

```python
def jira_to_coe_event(issue: JiraIssue) -> CoeEvent
def wiz_to_coe_event(issue: WizIssue) -> CoeEvent
def crowdstrike_to_coe_event(detect: CrowdstrikeDetect) -> CoeEvent
def vibranium_to_coe_event(incident: VibraniumIncident) -> CoeEvent
```

Each normalizer:
- Sets `source` to the appropriate `Source` enum.
- Sets `source_id` to the source's natural ID (`issue.key`, `issue.id`,
  `detect.id`, `incident.id`).
- Sets `severity` via `normalize_severity(source, raw_value)`.
- Sets `title`, `status`, `opened_at`, `updated_at` from the source record.
- Stashes the full original payload on the `raw` JSON column.
- Owner email comes from the source's assignee field; manager email is
  set later by the owner resolver.

Use `structlog.get_logger(__name__)` at the top of the module.

**Testing:**
Tests must verify each AC listed above:
- AC2.1: For each `Source × CoeSeverity` pair in `SEVERITY_MAP`, calling
  `normalize_severity(source, raw)` returns the mapped enum. Parameterize
  via `pytest.mark.parametrize`.
- AC2.2: `normalize_severity(Source.JIRA, "Low")` returns
  `CoeSeverity.UNKNOWN` and the warning was emitted (capture with
  `structlog.testing.capture_logs`); the log record contains `source` and
  `raw_value`.
- AC2.1: Each `*_to_coe_event` function applied to a representative
  source-typed record produces a `CoeEvent` whose fields match
  expectations (source, source_id, severity, title, opened_at, raw).
- Round-trip: the `raw` column equals the source model's `.model_dump()`.

**Verification:**
```bash
cd /Users/dmar/src/COE-overview
uv run pytest tests/test_normalize.py -v
uv run ruff check coe tests
uv run mypy coe tests
```

**Commit:** `feat(normalize): severity mapping + per-source to_coe_event`
<!-- END_TASK_1 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 2-3) -->

<!-- START_TASK_2 -->
### Task 2: Employee sync helper (`coe/db/employees.py`)

**Verifies:** N/A directly — supports AC2.3 / AC2.4 by populating the
`employees` table. (The Phase 4 pipeline calls this; tests here exercise
the upsert behavior.)

**Files:**
- Create: `/Users/dmar/src/COE-overview/coe/db/employees.py`
- Test: `/Users/dmar/src/COE-overview/tests/db/test_employees_sync.py`
  (integration — requires Postgres)

**Implementation:**

Expose:

```python
async def upsert_employees(
    session: AsyncSession, records: Iterable[Employee],
) -> int
```

- Where `Employee` is the Pydantic model from `coe/ingest/hr.py`.
- Uses Postgres `INSERT ... ON CONFLICT (email) DO UPDATE` to upsert each
  record into `employees`. Updates `manager_email`, `org_path`, and bumps
  `last_synced_at` to `now()` on every row whether inserted or updated.
- Returns the number of rows processed.

**Testing:**
Integration test against the docker-compose Postgres (or
`testcontainers.postgres`):
- Given an empty `employees` table, upserting 3 records inserts 3 rows.
- Running the same upsert again is idempotent — row count unchanged,
  `last_synced_at` advanced.
- Modifying `manager_email` on one input and re-upserting updates that
  row only.

**Verification:**
```bash
docker compose up -d postgres
uv run alembic upgrade head
uv run pytest tests/db/test_employees_sync.py -v -m integration
```

**Commit:** `feat(db): upsert helper for employees table`
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Owner resolver (`coe/owner_resolver.py`)

**Verifies:** AC2.3, AC2.4.

**Files:**
- Create: `/Users/dmar/src/COE-overview/coe/owner_resolver.py`
- Test: `/Users/dmar/src/COE-overview/tests/test_owner_resolver.py` (unit)
- Test: `/Users/dmar/src/COE-overview/tests/db/test_owner_resolver_db.py`
  (integration)

**Implementation:**

Two layers. The pure layer is the unit of testability; the DB layer is a
thin loader on top.

```python
@dataclass(frozen=True)
class ResolvedOwner:
    owner_email: str | None
    manager_email: str | None
    missing_owner_in_hr: bool


class OwnerResolver:
    def __init__(self, employees: Mapping[str, str | None]) -> None:
        """employees: { owner_email: manager_email_or_None } (case-insensitive)."""
        self._table = {k.lower(): v for k, v in employees.items()}

    def resolve(self, owner_email: str | None) -> ResolvedOwner:
        if owner_email is None:
            return ResolvedOwner(None, None, missing_owner_in_hr=False)
        normalized = owner_email.lower()
        if normalized in self._table:
            return ResolvedOwner(
                owner_email=owner_email,
                manager_email=self._table[normalized],
                missing_owner_in_hr=False,
            )
        return ResolvedOwner(
            owner_email=owner_email,
            manager_email=None,
            missing_owner_in_hr=True,
        )


async def load_resolver(session: AsyncSession) -> OwnerResolver:
    """Pulls the entire employees table into memory once per pipeline run."""
    rows = (
        await session.execute(
            select(EmployeeORM.email, EmployeeORM.manager_email)
        )
    ).all()
    return OwnerResolver({email: manager for email, manager in rows})
```

`EmployeeORM` is the SQLAlchemy model from `coe/db/models.py` (rename the
import locally to avoid clash with the Pydantic `Employee`).

**Testing:**

**Unit tests** of `OwnerResolver` (no DB):
- AC2.3: Resolver constructed with `{"alice@x.com": "manager@x.com"}`,
  resolving `"Alice@X.com"` returns `manager_email="manager@x.com"` and
  `missing_owner_in_hr=False`. (Case-insensitive on owner email.)
- AC2.4: Resolving `"unknown@x.com"` returns `manager_email=None` and
  `missing_owner_in_hr=True`.
- A `None` owner email returns all-None with `missing_owner_in_hr=False`
  (different from "in HR but no manager").

**Integration test** of `load_resolver` (requires Postgres):
- Seed the `employees` table with two rows; call `load_resolver`; verify
  the returned `OwnerResolver` resolves both correctly.

**Verification:**
```bash
uv run pytest tests/test_owner_resolver.py tests/db/test_owner_resolver_db.py -v
```

**Commit:** `feat(owner-resolver): pure resolver + db-backed loader`
<!-- END_TASK_3 -->

<!-- END_SUBCOMPONENT_B -->

---

## Done When

- [ ] `coe/normalize.py` exists with `SEVERITY_MAP`, `normalize_severity`,
  and four `*_to_coe_event` functions.
- [ ] `coe/db/employees.py` exists with `upsert_employees`.
- [ ] `coe/owner_resolver.py` exists with `OwnerResolver` and
  `load_resolver`.
- [ ] All test files in this phase pass (`pytest`).
- [ ] `uv run ruff check . && uv run mypy coe tests` exit 0.
- [ ] Branch pushed.

## Notes for Subsequent Phases

- Phase 4's pipeline calls `load_resolver(session)` once at the start of
  each run, then for each source record: `normalize_*_to_coe_event(...)`
  → `resolver.resolve(event.owner_email)` → set `manager_email` and
  `missing_owner_in_hr` on the event before upserting.
- The unmapped-severity warning surfaces in Phase 4's `coe_runs.errors_json`
  audit — Phase 4 captures the logger output via structlog and writes a
  per-run summary.
