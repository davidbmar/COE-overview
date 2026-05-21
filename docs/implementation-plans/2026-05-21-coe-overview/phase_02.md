# COE Overview — Phase 2: Source Ingest Clients

**Goal:** Read-only HTTP clients for Jira, Wiz, CrowdStrike, Vibranium, and
the internal HR service. Each client returns typed records updated since a
given timestamp, with structured error types for auth and transient
failures shared across sources.

**Architecture:** Async-first with `httpx`. One small shared base
(`ingest/base.py`) handles auth header injection, retry-with-backoff via
`tenacity`, `Retry-After` handling, and translation of HTTP error codes
into structured exceptions. Each source has its own thin module that knows
its own auth shape, query shape, and pagination. Tests use `respx` to mock
httpx at the transport layer.

**Tech Stack:** `httpx` (async), `tenacity` (retry), `respx` (test mocks),
`pydantic` (typed records).

**Scope:** Phase 2 of 7. Functionality. No DB writes — clients return
in-memory typed iterables. The pipeline (Phase 4) is what writes to
Postgres.

**Codebase verified:** 2026-05-21 — Phase 1 has landed
(`coe/config.py`, `coe/db/models.py`, `pyproject.toml` with `httpx`,
`pydantic`, `pytest`, `pytest-asyncio`). `coe/ingest/` does not exist.

---

## Acceptance Criteria Coverage

This phase implements and tests:

### coe-overview.AC1: Source clients pull deltas
- **coe-overview.AC1.1 Success:** Given a `since` timestamp, the Jira client returns
  tickets in the configured COE project allowlist whose `updated` field is
  greater than or equal to `since` (boundary-inclusive; idempotency is
  preserved by the upsert layer), paginated until exhausted.
- **coe-overview.AC1.2 Success:** The Wiz, CrowdStrike, and Vibranium clients each return
  records updated since the given timestamp, filtered to severity High or
  Critical per the documented per-source mapping.
- **coe-overview.AC1.3 Success:** The internal HR client returns the full set of active
  employee records, each with `email`, `manager_email`, and `org_path`.
- **coe-overview.AC1.4 Failure:** A 401 or 403 from a source surfaces as a structured
  `AuthError` for that source and does not abort other sources' runs.
- **coe-overview.AC1.5 Failure:** A 5xx response is retried with backoff (respecting
  `Retry-After` if present), up to a configured max-retry cap, after which
  it surfaces as a structured `TransientError`.

> Note: AC1.4 / AC1.5 "doesn't abort other sources" is enforced by the
> Phase 4 pipeline, not the client. This phase covers each client *raising*
> the structured error correctly; Phase 4 covers per-source isolation.

---

## External dependency findings (2026)

- **Jira REST API v3** uses `/rest/api/3/search/jql` (the `/search`
  endpoint is gone). Basic auth: `base64(email:api_token)`. Pagination via
  `nextPageToken`. ISO 8601 timestamps in JQL.
  📖 https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/
- **Wiz** is GraphQL-only. OAuth2 client credentials → 30-min token. Rate
  limit 10/s/user, 100/s/tenant. Severity enum:
  `INFORMATIONAL | LOW | MEDIUM | HIGH | CRITICAL`.
- **CrowdStrike Falcon** uses OAuth2 client credentials → 30-min token.
  Two-call pattern: `/detects/queries/detects/v1` returns IDs;
  `/detects/entities/summaries/GET/v1` returns full records. FQL filter
  syntax. Severity is numeric 0–100; ≥70 = High, ≥90 = Critical.
- **httpx + respx** is the prevailing async client + mock pattern in 2026.
  Use `tenacity` (or `stamina`) for retry/backoff with jitter.
- **Retry semantics:** Retry on 408, 429, 5xx; never retry 400/401/403/404.
  Honor `Retry-After` if present (seconds, ms, or HTTP-date).

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->

<!-- START_TASK_1 -->
### Task 1: Shared error types (`coe/ingest/errors.py`)

**Verifies:** AC1.4 (structured AuthError), AC1.5 (structured TransientError).

**Files:**
- Create: `/Users/dmar/src/COE-overview/coe/ingest/__init__.py`
- Create: `/Users/dmar/src/COE-overview/coe/ingest/errors.py`
- Test: `/Users/dmar/src/COE-overview/tests/ingest/test_errors.py` (unit)

**Implementation:**

`errors.py` defines three exception types, all carrying a `source: str`
attribute so the caller can tell which source failed:

- `IngestError(Exception)` — base. Fields: `source`, `message`.
- `AuthError(IngestError)` — raised for 401/403. Permanent; do not retry.
- `TransientError(IngestError)` — raised after retries are exhausted on a
  retriable status (408/429/5xx) or on connection errors. Carries
  `last_status: int | None` and `retries_attempted: int`.

```python
from __future__ import annotations

from dataclasses import dataclass


class IngestError(Exception):
    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"[{source}] {message}")
        self.source = source
        self.message = message


class AuthError(IngestError):
    """Auth failed (401/403). Permanent; do not retry."""


@dataclass
class TransientError(IngestError):
    """Retries exhausted on a retriable status or transport error."""

    def __init__(
        self,
        source: str,
        message: str,
        last_status: int | None,
        retries_attempted: int,
    ) -> None:
        super().__init__(source, message)
        self.last_status = last_status
        self.retries_attempted = retries_attempted
```

**Testing:**
Tests must verify each AC listed above:
- AC1.4: Instantiating `AuthError("jira", "...")` produces an exception
  whose `source == "jira"` and whose `str(...)` contains the source tag.
- AC1.5: `TransientError` exposes `last_status` and `retries_attempted`
  and is distinguishable from `AuthError` via `isinstance` checks.

Unit tests only. No HTTP.

**Verification:**
```bash
cd /Users/dmar/src/COE-overview
uv run pytest tests/ingest/test_errors.py -v
uv run ruff check coe/ingest tests/ingest
uv run mypy coe/ingest tests/ingest
```
Expected: tests pass; ruff and mypy exit 0.

**Commit:** `feat(ingest): structured AuthError / TransientError types`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Shared HTTP helper (`coe/ingest/base.py`)

**Verifies:** AC1.4, AC1.5 (the retry-and-translate logic — each source
client delegates to this).

**Files:**
- Create: `/Users/dmar/src/COE-overview/coe/ingest/base.py`
- Test: `/Users/dmar/src/COE-overview/tests/ingest/test_base.py` (unit, with respx)

**Implementation:**

`base.py` exposes one function:

```python
async def request_with_retry(
    client: httpx.AsyncClient,
    source: str,
    method: str,
    url: str,
    *,
    max_retries: int = 5,
    json: Any | None = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response
```

Behavior:
- On 200/2xx: returns the response.
- On 401/403: raises `AuthError(source, f"HTTP {status}")`.
- On 408/429/5xx: retries up to `max_retries` with exponential backoff +
  jitter. If `Retry-After` is present, sleep at least that long (parse as
  integer seconds first; if that fails, parse as HTTP-date). Final failure
  raises `TransientError(source, ..., last_status=...,
  retries_attempted=...)`.
- On `httpx.TransportError` / `httpx.ConnectError` / `httpx.ReadTimeout`:
  treat as retriable; final failure raises `TransientError` with
  `last_status=None`.
- On other 4xx (400/404/etc): raises `IngestError` directly — these are
  bugs in our code, not transient or auth issues.

Use `tenacity.AsyncRetrying` with `stop_after_attempt`, `wait_exponential`,
and a custom predicate. Compute the sleep as
`max(retry_after_seconds_if_present, exponential_backoff)`.

**Testing:**
Tests must verify each AC listed above using `respx` to script responses:
- AC1.4: A mocked 401 raises `AuthError` on the first call with no
  retries; a mocked 403 likewise.
- AC1.5: A mocked sequence of three 500s followed by a 200 returns the
  200. A mocked sequence of `max_retries + 1` 500s raises
  `TransientError` with `last_status=500` and `retries_attempted=max_retries`.
- AC1.5: A mocked 429 with `Retry-After: 1` sleeps ≥1s before retry
  (assert via clock advancement / monkey-patched sleep).
- A mocked transport error followed by a 200 retries and succeeds.

Use `pytest-asyncio` and `respx.mock()`. For sleep assertions, monkeypatch
`asyncio.sleep` and capture the durations.

**Verification:**
```bash
uv run pytest tests/ingest/test_base.py -v
```
Expected: all tests pass.

**Commit:** `feat(ingest): shared retry+error helper using tenacity+httpx`
<!-- END_TASK_2 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 3-7) -->

<!-- START_TASK_3 -->
### Task 3: Jira client (`coe/ingest/jira.py`)

**Verifies:** AC1.1, AC1.4, AC1.5.

**Files:**
- Modify: `/Users/dmar/src/COE-overview/coe/config.py` (add Jira settings)
- Create: `/Users/dmar/src/COE-overview/coe/ingest/jira.py`
- Test: `/Users/dmar/src/COE-overview/tests/ingest/test_jira.py` (unit, respx)

**Implementation:**

Extend `Settings` in `coe/config.py`:
```python
from pydantic import field_validator

class Settings(BaseSettings):
    # ... existing fields ...
    jira_base_url: str = "https://capsule.atlassian.net"
    jira_user_email: str = ""
    jira_api_token: str = ""
    jira_projects: list[str] = []  # COE allowlist, e.g. ["SEC", "OPS"]

    @field_validator("jira_projects", mode="before")
    @classmethod
    def _split_csv_jira_projects(cls, v: object) -> object:
        """Accept comma-separated env strings (`JIRA_PROJECTS=SEC,OPS`) as well as
        JSON (`JIRA_PROJECTS=["SEC","OPS"]`). pydantic-settings v2 expects JSON
        for complex env types by default; this validator widens that to also
        accept the comma-separated form K8s ConfigMaps typically use."""
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v
```

Read these from env (`JIRA_BASE_URL`, `JIRA_USER_EMAIL`, etc.). The
validator above means `JIRA_PROJECTS=SEC,OPS` and
`JIRA_PROJECTS=["SEC","OPS"]` both work; the K8s ConfigMap in Phase 7
uses the comma-separated form.

`jira.py` exposes:

```python
async def fetch_updated_since(
    since: datetime, settings: Settings | None = None
) -> AsyncIterator[JiraIssue]
```

- Uses `POST {base_url}/rest/api/3/search/jql`.
- Auth: `Authorization: Basic base64(email:api_token)`.
- Body: `{"jql": "project IN (...) AND updated >= '<since iso>'",
  "maxResults": 100, "nextPageToken": "..."}`.
- JQL timestamp format: ISO 8601 with timezone, e.g.
  `"2026-05-21 14:00"` (Jira accepts this loose form; use UTC).
- Iterates pages until `isLast: true` or no `nextPageToken`.
- Each yielded `JiraIssue` is a Pydantic model with `key`, `summary`,
  `priority`, `status`, `assignee_email`, `updated`, `created`, `raw_payload`.
- All HTTP calls go through `request_with_retry(client, "jira", ...)`.

**Testing:**
Tests must verify each AC listed above using respx:
- AC1.1: Given a mocked single-page response with 3 issues, the function
  yields 3 `JiraIssue` models with the expected fields. The request body
  must contain the JQL constructed from `since`.
- AC1.1: Given a mocked two-page response, both pages are yielded; verify
  the second request carries the `nextPageToken` from page 1's response.
- AC1.1: The `jira_projects` allowlist appears in the JQL.
- AC1.4: A mocked 401 raises `AuthError("jira", ...)`.
- AC1.5: A mocked 503 (3x) then 200 succeeds and yields data.

**Verification:**
```bash
uv run pytest tests/ingest/test_jira.py -v
```

**Commit:** `feat(ingest): jira client with JQL filter and cursor pagination`
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Wiz client (`coe/ingest/wiz.py`)

**Verifies:** AC1.2 (Wiz portion), AC1.4, AC1.5.

**Files:**
- Modify: `coe/config.py` (add `wiz_client_id`, `wiz_client_secret`, `wiz_api_url`, `wiz_auth_url`)
- Create: `coe/ingest/wiz.py`
- Test: `tests/ingest/test_wiz.py` (unit, respx)

**Implementation:**

`wiz.py` exposes:
```python
async def fetch_updated_since(
    since: datetime, settings: Settings | None = None
) -> AsyncIterator[WizIssue]
```

Two-step:
1. **Token:** `POST {wiz_auth_url}` with form-encoded body
   `client_id=...&client_secret=...&grant_type=client_credentials&audience=wiz-api`.
   Returns `{"access_token": "...", "expires_in": 1800}`. Cache the token
   for `expires_in - 60` seconds via a module-level mutable cache so
   subsequent calls in the same run reuse it.
2. **Query:** `POST {wiz_api_url}` with GraphQL:

   ```graphql
   query IssuesPage($filter: IssueFilters, $first: Int, $after: String) {
     issues(filterBy: $filter, first: $first, after: $after) {
       pageInfo { hasNextPage endCursor }
       nodes {
         id severity status updatedAt createdAt
         entitySnapshot { name nativeType }
         projects { id name }
         assignee { email }
       }
     }
   }
   ```

   Variables: `{"filter": {"severity": ["HIGH", "CRITICAL"],
   "updatedAt": {"after": "<since iso>"}}, "first": 100, "after": null}`.

`WizIssue` Pydantic model: `id: str`, `severity: str` (the GraphQL
string value — `"CRITICAL"`, `"HIGH"`, etc. — NOT a Python `Enum`; Phase 3's
`SEVERITY_MAP[Source.WIZ]` is keyed by these string values), `status: str`,
`entity_name: str | None`, `assignee_email: str | None`,
`updated_at: datetime`, `created_at: datetime`, `raw_payload: dict`.

All HTTP through `request_with_retry(..., "wiz", ...)`.

**Testing:**
Tests must verify each AC listed above using respx:
- AC1.2: Given a mocked token response + single-page issues response,
  yields the expected `WizIssue` objects. Token endpoint must be called
  with `grant_type=client_credentials`.
- AC1.2: The GraphQL request body contains `["HIGH", "CRITICAL"]` and
  the `since` ISO timestamp.
- AC1.2: Two-page response is paginated via `endCursor`.
- AC1.2: Token cache: making two `fetch_updated_since` calls in the same
  test should hit the token endpoint only once.
- AC1.4: A 401 from the GraphQL endpoint raises `AuthError("wiz", ...)`.
- AC1.5: 5xx retried, final 5xx raises `TransientError("wiz", ...)`.

**Verification:**
```bash
uv run pytest tests/ingest/test_wiz.py -v
```

**Commit:** `feat(ingest): wiz graphql client with token cache`
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: CrowdStrike client (`coe/ingest/crowdstrike.py`)

**Verifies:** AC1.2 (CrowdStrike portion), AC1.4, AC1.5.

**Files:**
- Modify: `coe/config.py` (add `crowdstrike_client_id`, `crowdstrike_client_secret`, `crowdstrike_base_url`)
- Create: `coe/ingest/crowdstrike.py`
- Test: `tests/ingest/test_crowdstrike.py` (unit, respx)

**Implementation:**

`crowdstrike.py` exposes:
```python
async def fetch_updated_since(
    since: datetime, settings: Settings | None = None
) -> AsyncIterator[CrowdstrikeDetect]
```

Three-step:
1. **Token:** `POST {base_url}/oauth2/token` with form body
   `client_id=...&client_secret=...&grant_type=client_credentials`.
   Cache for `expires_in - 60` seconds.
2. **Query IDs:** `GET {base_url}/detects/queries/detects/v1` with
   FQL `filter=max_severity:>=70+last_updated:>='<since iso>'` and
   `limit=1000`. Offset pagination via `offset` param. Returns
   `{"resources": ["detection-id-1", ...]}`.
3. **Summaries:** `POST {base_url}/detects/entities/summaries/GET/v1`
   with body `{"ids": [<batch of up to 1000>]}`. Returns full records.

`CrowdstrikeDetect` Pydantic model: `id`, `max_severity` (int),
`severity_name` (derived: `>=90 → CRITICAL`, `>=70 → HIGH`, else passthrough),
`status`, `last_updated`, `assigned_to_uid`, `raw_payload`.

Auth header on steps 2 and 3: `Authorization: Bearer <token>`.

All HTTP through `request_with_retry(..., "crowdstrike", ...)`.

**Testing:**
Tests must verify each AC listed above using respx:
- AC1.2: Mocked token + IDs (5 detection IDs) + summaries response yields
  5 `CrowdstrikeDetect` records.
- AC1.2: FQL filter in the IDs request contains `max_severity:>=70` and
  the `since` ISO timestamp.
- AC1.2: Pagination: 1500 IDs requires two `queries/detects` calls (offset
  0 then 1000) and two `entities/summaries` POSTs.
- AC1.2: `severity_name` is `CRITICAL` for max_severity=90+, `HIGH` for
  70–89.
- AC1.4: 401 from the IDs call raises `AuthError("crowdstrike", ...)`.
- AC1.5: 5xx with `Retry-After: 2` is retried, final failure raises
  `TransientError`.

**Verification:**
```bash
uv run pytest tests/ingest/test_crowdstrike.py -v
```

**Commit:** `feat(ingest): crowdstrike client with two-step queries+summaries`
<!-- END_TASK_5 -->

<!-- START_TASK_6 -->
### Task 6: Vibranium client (`coe/ingest/vibranium.py`)

**Verifies:** AC1.2 (Vibranium portion), AC1.4, AC1.5.

**Files:**
- Modify: `coe/config.py` (add `vibranium_base_url`, `vibranium_api_token`)
- Create: `coe/ingest/vibranium.py`
- Test: `tests/ingest/test_vibranium.py` (unit, respx)

**⚠ Implementation note:** Vibranium is an internal tool. Its API
endpoint paths and response shape are not in this plan — they live in
the internal Vibranium docs. **Before starting this task, the engineer
must:** (a) pull the Vibranium API docs, (b) confirm the
endpoint(s) for "list incidents updated since X with severity filter,"
(c) confirm pagination shape. Update the placeholders below before
implementing.

**Implementation skeleton:**

```python
async def fetch_updated_since(
    since: datetime, settings: Settings | None = None
) -> AsyncIterator[VibraniumIncident]
```

- Auth: `Authorization: Bearer {vibranium_api_token}`.
- Endpoint: `GET {base_url}/<PATH-TO-CONFIRM>?since=<iso>&severity=high,critical`.
- Pagination: cursor or offset, per internal docs.
- `VibraniumIncident` Pydantic model fields to confirm with the docs,
  but should at minimum include: `id: str`, `severity: str` (the raw
  string value from Vibranium — NOT a Python `Enum`; Phase 3's
  `SEVERITY_MAP[Source.VIBRANIUM]` will key against these strings),
  `status: str`, `assignee_email: str | None`, `updated_at: datetime`,
  `created_at: datetime`, `raw_payload: dict`.
- All HTTP through `request_with_retry(..., "vibranium", ...)`.

**Testing:**
Same shape as the other source clients:
- AC1.2: Given the docs-confirmed endpoint, yields incidents with the
  expected fields.
- AC1.2: Pagination works.
- AC1.4: 401/403 raises `AuthError("vibranium", ...)`.
- AC1.5: 5xx retried; final failure raises `TransientError`.

**Verification:**
```bash
uv run pytest tests/ingest/test_vibranium.py -v
```

**Commit:** `feat(ingest): vibranium client (internal API)`

**Risk callout:** If Vibranium docs reveal a non-REST shape (e.g.
streaming, websockets, or batch dump), surface that to the planner — the
abstraction may need to change.
<!-- END_TASK_6 -->

<!-- START_TASK_7 -->
### Task 7: Internal HR client (`coe/ingest/hr.py`)

**Verifies:** AC1.3.

**Files:**
- Modify: `coe/config.py` (add `hr_base_url`, `hr_api_token`)
- Create: `coe/ingest/hr.py`
- Test: `tests/ingest/test_hr.py` (unit, respx)

**⚠ Implementation note:** The internal HR service contract is one of the
"open risks" in the design plan. The engineer must confirm the endpoint
path, auth shape, and whether the API exposes manager chains or just
direct manager. Adjust below before implementing.

**Implementation:**

`hr.py` exposes:
```python
async def fetch_all_active_employees(
    settings: Settings | None = None,
) -> AsyncIterator[Employee]
```

- The full employee directory is expected to be small enough to pull as a
  single dump per run (no delta semantics). If pagination is exposed,
  iterate to completion.
- `Employee` Pydantic model: `email`, `manager_email`, `org_path`,
  `is_active`.
- Only `is_active=True` records are yielded.
- All HTTP through `request_with_retry(..., "hr", ...)`.

**Testing:**
- AC1.3: Mocked HR response with 3 employees yields 3 `Employee` models
  with `email`, `manager_email`, `org_path` populated.
- AC1.3: Inactive employees in the response are filtered out.
- AC1.4: 401 raises `AuthError("hr", ...)`.
- AC1.5: 5xx retried; final failure raises `TransientError`.

**Verification:**
```bash
uv run pytest tests/ingest/test_hr.py -v
```

**Commit:** `feat(ingest): internal HR client for employee directory`
<!-- END_TASK_7 -->

<!-- END_SUBCOMPONENT_B -->

---

## Done When

- [ ] `coe/ingest/{errors,base,jira,wiz,crowdstrike,vibranium,hr}.py` all
  exist and import cleanly.
- [ ] Every client's test file (5 in total) passes.
- [ ] `tests/ingest/test_errors.py` and `tests/ingest/test_base.py` pass.
- [ ] `uv run ruff check . && uv run mypy coe tests && uv run pytest` all
  exit 0.
- [ ] Open risks logged for Vibranium (Task 6) and HR (Task 7) before
  merging if their API contracts are not yet confirmed.
- [ ] Branch pushed.

## Notes for Subsequent Phases

- Phase 3 will consume the typed records from these clients via
  `to_coe_event(raw, source)` normalizers.
- Phase 4's pipeline orchestrator wraps `fetch_updated_since` calls per
  source in `asyncio.gather(..., return_exceptions=True)` — that's where
  the "doesn't abort other sources" half of AC1.4 lives.
- The `raw_payload` field on every model carries the original JSON so we
  can stash it in the `*_raw` audit tables in Phase 4.
