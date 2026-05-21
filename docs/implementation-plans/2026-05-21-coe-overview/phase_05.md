# COE Overview — Phase 5: Google Doc Renderer

**Goal:** Generate the Monday prep Google Doc from current Postgres state,
with sections for new / changed / missing-owner / missing-SLA / recently-
resolved events, and source-record links per row.

**Architecture:** Three small modules. `coe/doc/sections.py` is pure SQL:
given a `RunContext` (since timestamp + run_id), it returns five lists of
`CoeEvent` rows for the five sections. `coe/doc/google_docs.py` is a thin
wrapper over `google-api-python-client` — `create_doc(name, folder_id)`
and `batch_update(doc_id, requests)`. `coe/doc/renderer.py` composes the
two: pulls the sections, builds a batchUpdate request list, creates the
doc, applies the updates, returns the doc URL.

**Tech Stack:** `google-api-python-client` (sync), `google-auth`, SQLAlchemy.
The Google API client is sync; we run it via `asyncio.to_thread(...)` from
the async caller.

**Scope:** Phase 5 of 7. Functionality. The renderer is invoked separately
from the ingest pipeline (e.g. after the ingest CronJob completes, by the
same job or a follow-on job — Phase 7 wires this up). For now it's a
standalone module with its own entrypoint at `coe/doc/__main__.py`.

**Codebase verified:** 2026-05-21 — Phases 1–4 have landed.
`coe/db/models.py` exposes `CoeEvent`, `CoeRun`. `coe/db/runs.py` exposes
`RunContext`. `coe/doc/` does not exist.

---

## Acceptance Criteria Coverage

This phase implements and tests:

### coe-overview.AC4: Google Doc rendering
- **coe-overview.AC4.1 Success:** The renderer produces a Google Doc containing sections
  for: new events, changed events, events missing owner, events missing
  SLA, and recently resolved events.
- **coe-overview.AC4.2 Success:** Each event row in the doc includes a clickable link to
  its source record (Jira ticket, Wiz finding, CrowdStrike detection, or
  Vibranium incident).
- **coe-overview.AC4.3 Success:** Events with a null `owner_email` appear in the
  "events missing owner" section, not in the standard new/changed lists.
- **coe-overview.AC4.4 Failure:** A Docs API failure surfaces a structured error without
  rolling back or corrupting the Postgres source-of-truth state.

---

## External dependency findings (2026)

- **`google-api-python-client` 2.196+** is the official, sync-only client.
  Wrap blocking calls in `asyncio.to_thread`.
- **Required OAuth scopes:** `drive` (full) for file create + folder
  assignment, `documents` for batchUpdate.
- **Two-step pattern is current:** Drive `files.create(body={
  mimeType: 'application/vnd.google-apps.document', parents: [folder_id]})`
  → Docs `documents.batchUpdate(documentId=..., body={requests: [...]})`.
- **Rate limits:** 600 writes/min/project, 60/min/user. One run produces
  one create + one batchUpdate — well under limits.
- **Mock pattern for tests:** `googleapiclient.http.HttpMock` and
  `HttpMockSequence` — feed pre-canned responses; build the service with
  `http=mock` instead of credentials.
- **Idempotency:** Create a new doc per run (recommended). The
  `coe_runs.doc_url` column (added in this phase) records the URL for
  audit.

---

<!-- START_SUBCOMPONENT_A (task 1) -->

<!-- START_TASK_1 -->
### Task 1: Section query layer (`coe/doc/sections.py`)

**Verifies:** AC4.1 (data layer for sections), AC4.3 (missing-owner bucketing).

**Files:**
- Create: `/Users/dmar/src/COE-overview/coe/doc/__init__.py` (empty)
- Create: `/Users/dmar/src/COE-overview/coe/doc/sections.py`
- Test: `/Users/dmar/src/COE-overview/tests/doc/test_sections.py` (integration)

**Implementation:**

Define:

```python
@dataclass(frozen=True)
class DocSections:
    new: list[CoeEvent]
    changed: list[CoeEvent]
    missing_owner: list[CoeEvent]
    missing_sla: list[CoeEvent]
    recently_resolved: list[CoeEvent]


async def build_sections(
    session: AsyncSession,
    since: datetime,
    *,
    resolved_window: timedelta = timedelta(days=7),
) -> DocSections
```

Bucketing rules (apply in order — an event appears in exactly one
section):

1. `missing_owner` (AC4.3): `coe_review_status` is not `'resolved'` AND
   `owner_email IS NULL`.
2. `missing_sla`: not in `missing_owner` AND `coe_review_status` is not
   `'resolved'` AND `sla_due_at IS NULL`.
3. `new`: not in the above AND `opened_at > since`.
4. `changed`: not in the above AND `opened_at <= since` AND
   `updated_at > since`.
5. `recently_resolved`: `coe_review_status = 'resolved'` AND
   `updated_at >= now() - resolved_window`.

Within each section, sort by `severity` (CRITICAL first, then HIGH,
UNKNOWN last), then `updated_at DESC`.

**Testing:**
Integration tests against the testcontainer Postgres, seeding `coe_events`
directly:
- AC4.1: Seed events covering each bucket; `build_sections` returns the
  expected partition. Total of input events == sum of section lengths.
- AC4.3: An event with `owner_email=None` and `status='open'` lands in
  `missing_owner`, not in `new` even if its `opened_at > since`.
- An event with both `owner_email=None` AND `sla_due_at=None` lands in
  `missing_owner` (precedence rule).
- A resolved event updated 8 days ago is excluded from `recently_resolved`
  with a 7-day window.

**Verification:**
```bash
uv run pytest tests/doc/test_sections.py -v -m integration
```

**Commit:** `feat(doc): section query layer for weekly prep doc`
<!-- END_TASK_1 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 2-3) -->

<!-- START_TASK_2 -->
### Task 2: Google API client wrapper (`coe/doc/google_docs.py`)

**Verifies:** Supports AC4.1 / AC4.4 — provides the API surface the
renderer composes against. Tests verify error translation (AC4.4).

**Files:**
- Modify: `pyproject.toml` (add `google-api-python-client>=2.196.0`,
  `google-auth>=2.30.0` to `dependencies`)
- Modify: `coe/config.py` (add `google_service_account_file: str`,
  `google_drive_folder_id: str`)
- Create: `/Users/dmar/src/COE-overview/coe/doc/google_docs.py`
- Test: `/Users/dmar/src/COE-overview/tests/doc/test_google_docs.py` (unit)

**Implementation:**

`google_docs.py` exposes a small class:

```python
SCOPES = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
)


@dataclass(frozen=True)
class DocsClient:
    drive: Any   # googleapiclient.discovery.Resource
    docs: Any    # ...


def build_clients_from_file(service_account_file: str) -> DocsClient:
    creds = service_account.Credentials.from_service_account_file(
        service_account_file, scopes=list(SCOPES)
    )
    return DocsClient(
        drive=build("drive", "v3", credentials=creds, cache_discovery=False),
        docs=build("docs", "v1", credentials=creds, cache_discovery=False),
    )


async def create_doc(client: DocsClient, name: str, folder_id: str) -> str:
    """Returns the new document's ID. Raises GoogleDocsError on API failure."""

async def batch_update(
    client: DocsClient, document_id: str, requests: list[dict]
) -> None:
    """Applies a single batchUpdate. Raises GoogleDocsError on API failure."""


def make_doc_url(document_id: str) -> str:
    return f"https://docs.google.com/document/d/{document_id}/edit"
```

`create_doc` and `batch_update` wrap blocking calls via `asyncio.to_thread`.
Both translate `googleapiclient.errors.HttpError` into a custom
`GoogleDocsError(Exception)` carrying the status code and Google's error
message — so the renderer (and Phase 5 tests) can assert AC4.4 without
caring about Google's exception types.

```python
class GoogleDocsError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"[google-docs {status}] {message}")
        self.status = status
```

**Testing:**
Unit tests using `googleapiclient.http.HttpMock` / `HttpMockSequence`:
- `create_doc` posts to `drive.files.create` with the right body
  (`mimeType: application/vnd.google-apps.document`, `parents: [folder_id]`)
  and returns the mocked file ID.
- `batch_update` posts the requests array to
  `docs.documents.batchUpdate` and returns `None` on 200.
- AC4.4: A mocked 403 on `files.create` raises `GoogleDocsError(403, ...)`.
- AC4.4: A mocked 500 on `batchUpdate` raises `GoogleDocsError(500, ...)`.

**Verification:**
```bash
uv run pytest tests/doc/test_google_docs.py -v
```

**Commit:** `feat(doc): google docs api wrapper with structured errors`
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Renderer (`coe/doc/renderer.py`)

**Verifies:** AC4.1, AC4.2, AC4.4.

**Files:**
- Modify: `coe/db/models.py` (add `doc_url: Mapped[str | None]` to
  `CoeRun`)
- Create: `/Users/dmar/src/COE-overview/alembic/versions/0002_coe_runs_doc_url.py`
  (autogen via `alembic revision --autogenerate --rev-id 0002 -m "coe_runs.doc_url"`)
- Create: `/Users/dmar/src/COE-overview/coe/doc/renderer.py`
- Create: `/Users/dmar/src/COE-overview/coe/doc/__main__.py`

**Implementation:**

`renderer.py` exposes:

```python
async def render_weekly_doc(
    session: AsyncSession,
    settings: Settings,
    run_id: int,
) -> str:
    """Returns the doc URL. Raises GoogleDocsError on API failure;
    Postgres state is unchanged in that case."""
```

Algorithm:
1. Read the `coe_runs` row for `run_id`. Use its `since` column directly
   as the delta window — this is the value `start_run` (Phase 4 Task 2)
   persisted when the run began, so Phase 4 and Phase 5 always agree on
   what "since last meeting" means. (We do NOT re-derive `since` from
   `find_prior_run_finished_at` here — by the time the doc renderer
   runs, the current run is itself the "most recent ok/partial run," so
   the helper would return the wrong value.) Build the doc title from
   `started_at`: `f"COE Prep — Week of {started_at:%Y-%m-%d}"`.
2. `sections = await build_sections(session, since)`.
3. `client = build_clients_from_file(settings.google_service_account_file)`.
4. `doc_id = await create_doc(client, title, settings.google_drive_folder_id)`.
5. `requests = build_requests(title, sections)` (pure function below).
6. `await batch_update(client, doc_id, requests)`.
7. Update the `coe_runs` row: set `doc_url = make_doc_url(doc_id)` (note
   the local variable is named `url` and the helper is renamed to
   `make_doc_url` in `google_docs.py` to avoid shadowing). Commit.
8. Return the URL.

`build_requests(title, sections) -> list[dict]` is a pure function: takes
the bucketed events and produces the Docs API batchUpdate request list.

Doc shape:
```
<H1> COE Prep — Week of YYYY-MM-DD
<H2> New events (N)
  • [SEV] <title>  —  owner: alice@…   sla: 2026-05-28   [link]
  • ...
<H2> Changed events (N)
  ...
<H2> Events missing owner (N)
  ...
<H2> Events missing SLA (N)
  ...
<H2> Recently resolved (N)
  ...
```

For each event row, AC4.2 requires a hyperlink to the source record. Use
`updateTextStyle` with `textStyle: {link: {url: ...}}` covering the
`[link]` token. Source URL per source:
- Jira: `{jira_base_url}/browse/{source_id}`
- Wiz: `https://app.wiz.io/issues/{source_id}`
- CrowdStrike: `https://falcon.crowdstrike.com/activity/detections/detail/{source_id}`
- Vibranium: `{vibranium_base_url}/incidents/{source_id}` (confirm with
  internal docs)

Insert content using `insertText` requests at index 1, in reverse
section order (Docs API inserts shift indices; building from the bottom
avoids the index-juggling). Or, simpler: use one `insertText` per
section + recompute indices. Either is acceptable — write helpers in
`renderer.py` to keep `build_requests` readable.

`coe/doc/__main__.py`: resolves `run_id` in priority order:

1. **CLI argument** — `sys.argv[1]` if provided.
2. **emptyDir handoff file** — reads
   `$COE_RUN_ID_PATH` (default `/var/run/coe/last_run_id`), which the
   Phase 4 ingest container writes at the end of its run (see Phase 7
   Task 3's CronJob manifest for the emptyDir volume mount).
3. **Fallback** — most recent `coe_runs` row with
   `status IN ('ok','partial')`. Logs a warning when this path is taken
   because it means the K8s handoff didn't happen as expected.

Concrete shape:

```python
import os
import sys
from pathlib import Path

RUN_ID_PATH = Path(os.environ.get("COE_RUN_ID_PATH", "/var/run/coe/last_run_id"))

async def resolve_run_id(session: AsyncSession) -> int:
    if len(sys.argv) > 1:
        return int(sys.argv[1])
    if RUN_ID_PATH.is_file():
        return int(RUN_ID_PATH.read_text().strip())
    # Last resort: latest ok/partial run
    log.warning("run-id-fallback", reason="no argv, no emptyDir handoff file")
    row_id = await session.scalar(
        select(CoeRun.id)
        .where(CoeRun.status.in_(["ok", "partial"]))
        .order_by(CoeRun.id.desc())
        .limit(1)
    )
    if row_id is None:
        raise SystemExit("no coe_runs row to render against")
    return row_id
```

Then `await render_weekly_doc(session, settings, run_id)`.

**Testing:**

See Task 4 for the integration tests covering AC4.1, AC4.2, AC4.3, AC4.4
end-to-end.

For this task, unit-test `build_requests` against canned `DocSections`
fixtures:
- `build_requests` for empty sections produces a title + section headers
  + "no events" lines, and no link styling requests.
- Each populated section produces one `insertText` per event plus link
  styling.

**Verification:**
```bash
uv run pytest tests/doc/test_renderer.py -v
uv run alembic upgrade head  # apply 0003 migration
```

**Commit:** `feat(doc): weekly prep renderer with sections + source links`
<!-- END_TASK_3 -->

<!-- END_SUBCOMPONENT_B -->

<!-- START_SUBCOMPONENT_C (task 4) -->

<!-- START_TASK_4 -->
### Task 4: End-to-end renderer integration test

**Verifies:** AC4.1, AC4.2, AC4.3, AC4.4.

**Files:**
- Create: `/Users/dmar/src/COE-overview/tests/doc/test_renderer_e2e.py`
  (integration — testcontainer DB, HttpMock for Google)

**Implementation:**

Combines the `session` fixture from Phase 4's `conftest.py` with
`HttpMockSequence` for Google.

Scenarios:
- AC4.1: Seed `coe_events` with at least one row per section. Mock
  `create_doc` to return `doc_id="abc"` and `batch_update` to return 200.
  Run `render_weekly_doc`. Assert: the mocked `batchUpdate` body
  contains text for the five section headings AND one row per seeded
  event. The returned URL matches the expected shape.
- AC4.2: Inspect the `batchUpdate` requests array — for each seeded
  event, exactly one `updateTextStyle` request has a `link.url`
  matching the source's expected URL pattern.
- AC4.3: Seed two events, one with `owner_email=None`. The
  `batchUpdate` body has the unowned event under the "Events missing
  owner" heading, not under "New" / "Changed".
- AC4.4: Mock `batch_update` to return 500. Assert `render_weekly_doc`
  raises `GoogleDocsError(500, ...)`. After the failure, the
  `coe_runs.doc_url` is unchanged (None), and `coe_events` is unchanged.

**Verification:**
```bash
uv run pytest tests/doc/test_renderer_e2e.py -v -m integration
```

**Commit:** `test(doc): e2e renderer tests covering AC4.1-AC4.4`
<!-- END_TASK_4 -->

<!-- END_SUBCOMPONENT_C -->

---

## Done When

- [ ] `coe/doc/{sections,google_docs,renderer,__main__}.py` exist.
- [ ] `coe_runs.doc_url` column added via a new alembic migration applied.
- [ ] All Phase 5 tests pass.
- [ ] Manual smoke: with a real service-account JSON and Drive folder
  shared with the SA, `uv run python -m coe.doc <run_id>` creates a doc
  visible in Drive with the expected sections.
- [ ] `uv run ruff check . && uv run mypy coe tests && uv run pytest`
  exit 0.
- [ ] Branch pushed.

## Notes for Subsequent Phases

- Phase 7's CronJob will run two containers (or two stages of one job):
  `python -m coe` then `python -m coe.doc`. The second uses the
  `coe_runs.id` of the run the first just produced.
- The service-account JSON file path is read from
  `GOOGLE_SERVICE_ACCOUNT_FILE` env var — Phase 7 mounts the SA key as a
  K8s secret.
