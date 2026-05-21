# COE Prep — Weekly Correction-of-Errors Aggregator

## Context

The Correction of Errors (COE) meeting runs every Monday and currently lacks a
consistent, machine-readable view of the events under review. Security and
engineering signal is scattered across Jira, Wiz, CrowdStrike, and Vibranium,
and ownership/SLA/priority for those events is inconsistent — some sources
carry owners, some don't, and gaps only surface during the meeting itself.

This system builds a structured, queryable source of truth for COE-eligible
events and generates the weekly prep doc from it to coordinate the meeting.
Postgres is the source of truth for *what to discuss*; the Google Doc and
Grafana are views over it. The system is read-only against external sources —
after the meeting, Matt manually updates Jira tickets and priorities in the
source systems. Next Monday's ingest naturally picks up those changes via the
delta-since-last-run model.

## Definition of Done

- A scheduled job runs each Monday morning and refreshes a Postgres store of
  COE-eligible events (new or changed since the previous Monday's run) from
  Jira, Wiz, CrowdStrike, and Vibranium, joined with employee/manager data
  from the internal HR service.
- A Google Doc for that Monday's meeting is generated from the Postgres state,
  including per-event ownership, SLA, priority, and gaps where any of those
  are missing.
- Grafana dashboards read directly from the same Postgres tables, giving the
  team a live view between meetings.
- After the meeting, Matt updates Jira and source-system priorities by hand —
  no writeback path in the system itself. Those changes show up automatically
  in next Monday's run via the delta model.

## Acceptance Criteria

### AC1: Source clients pull deltas

- **AC1.1 Success:** Given a `since` timestamp, the Jira client returns
  tickets in the configured COE project allowlist whose `updated` field is
  greater than or equal to `since` (the boundary inclusive form is used so
  records sharing the exact `since` timestamp are not missed; idempotency
  is preserved by the upsert layer), paginated until exhausted.
- **AC1.2 Success:** The Wiz, CrowdStrike, and Vibranium clients each return
  records updated since the given timestamp, filtered to severity High or
  Critical per the documented per-source mapping.
- **AC1.3 Success:** The internal HR client returns the full set of active
  employee records, each with `email`, `manager_email`, and `org_path`.
- **AC1.4 Failure:** A 401 or 403 from a source surfaces as a structured
  `AuthError` for that source and does not abort other sources' runs.
- **AC1.5 Failure:** A 5xx response is retried with backoff (respecting
  `Retry-After` if present), up to a configured max-retry cap, after which
  it surfaces as a structured `TransientError`.

### AC2: Normalization

- **AC2.1 Success:** Each source's native severity value maps to the
  `coe_severity` enum (`CRITICAL` or `HIGH`) per a documented per-source
  mapping table.
- **AC2.2 Failure:** A severity value not in the mapping table falls back to
  `UNKNOWN`, and a warning is logged including the source and the unmapped
  raw value.
- **AC2.3 Success:** Given an `owner_email` from a source record, the owner
  resolver returns the matching `manager_email` from the local `employees`
  table.
- **AC2.4 Failure:** An `owner_email` not present in `employees` resolves to
  `None`, and the resulting `coe_events` row is flagged with
  `missing_owner_in_hr=true`.

### AC3: Ingest pipeline

- **AC3.1 Success:** The pipeline reads the latest `last_successful_run_at`
  from `coe_runs` and uses it as the `since` parameter for each source
  client.
- **AC3.2 Success:** Running the pipeline twice in succession with no
  source-system changes between runs produces no diffs in `coe_events`
  business fields (upserts are idempotent). The `last_seen_at` metadata
  column IS updated on every run — that's how we record "the system saw
  this event in run N" — and is exempt from the no-diff guarantee.
- **AC3.3 Success:** A successful run writes a new `coe_runs` row with
  `status='ok'`, `finished_at` set, and `events_ingested` populated with
  the correct count.
- **AC3.4 Failure:** A failure from a single source produces a non-fatal
  entry in `coe_runs.errors_json` for that source and does not block the
  other sources from completing in the same run.
- **AC3.5 Bootstrap:** The first-ever run (no prior `coe_runs` row) uses a
  configured synthetic baseline timestamp, sets `is_bootstrap=true` on the
  `coe_runs` row, and ingests the current open backlog.

### AC4: Google Doc rendering

- **AC4.1 Success:** The renderer produces a Google Doc containing sections
  for: new events, changed events, events missing owner, events missing
  SLA, and recently resolved events.
- **AC4.2 Success:** Each event row in the doc includes a clickable link to
  its source record (Jira ticket, Wiz finding, CrowdStrike detection, or
  Vibranium incident).
- **AC4.3 Success:** Events with a null `owner_email` appear in the
  "events missing owner" section, not in the standard new/changed lists.
- **AC4.4 Failure:** A Docs API failure surfaces a structured error without
  rolling back or corrupting the Postgres source-of-truth state.

### AC5: Project scaffolding + DB schema (Infrastructure)

**Verifies: None.** Operational verification: dependency manager sync
succeeds, `alembic upgrade head` applies cleanly against a fresh Postgres,
`pytest` collects, `ruff check` and `mypy` pass on the empty scaffold.

### AC6: Grafana dashboards (Infrastructure)

**Verifies: None.** Operational verification: importing the checked-in JSON
into a Grafana instance with the Postgres datasource configured produces
panels that successfully query the COE tables.

### AC7: K8s CronJob deployment (Infrastructure)

**Verifies: None.** Operational verification: container image builds, the
manifest applies cleanly to a test namespace, and a manually-triggered job
produces a fresh `coe_runs` row.

## System Shape

```
              ┌────── Jira  ─────────┐
              ├────── Wiz   ─────────┤
ingest job ◄──┤────── CrowdStrike ───┤   (read-only)
(Mon AM)      ├────── Vibranium  ────┤
              └────── Internal HR ───┘
                       │
                       ▼
                   Postgres  ◄── source of truth (for the meeting)
                   │     │
                   ▼     ▼
           Google Doc   Grafana
              │
              ▼
           meeting → Matt updates Jira/sources by hand →
                                next Monday's ingest reflects it
```

Runtime: Python service deployed to internal Kubernetes as a CronJob,
triggered Monday morning before the meeting (exact time TBD with meeting
owner; system should record `last_successful_run_at` to anchor the
"since last meeting" delta rather than relying on a fixed schedule).

## Data Sources

| Source        | Auth                         | What we pull                                                |
|---------------|------------------------------|-------------------------------------------------------------|
| Jira          | service token                | Tickets in designated COE projects/queues (allowlist TBD)   |
| Wiz           | service token                | High/Critical findings only                                 |
| CrowdStrike   | service token                | High/Critical detections only                               |
| Vibranium     | REST + token (internal docs) | All incidents in scope (severity filter applied)            |
| Internal HR   | internal service API         | Employee → manager mapping for owner-resolution             |

**Eligibility filter:** Severity threshold (High / Critical) applied
cross-source. Severity normalization is non-trivial — each source uses a
different scale. Phase 1 should land a single `coe_severity` enum and a
per-source mapping table.

**Lookback model:** Delta since `last_successful_run_at`. Each ingest:
1. Reads `last_successful_run_at` from a `coe_runs` table.
2. Pulls items where `updated_at > last_successful_run_at` from each source.
3. Upserts into per-source raw tables and a unified `coe_events` table.
4. Writes a new `coe_runs` row on success.

This means the doc reflects what's new/changed for the meeting, not the full
standing backlog — the standing view lives in Grafana.

## Postgres Schema (sketch)

- `coe_events` — unified event table, one row per event across sources.
  Columns: `id`, `source` (jira/wiz/cs/vib), `source_id`, `title`, `severity`,
  `status`, `owner_email`, `manager_email`, `sla_due_at`, `priority`,
  `opened_at`, `updated_at`, `last_seen_at`, `coe_review_status`.
- Per-source raw tables (`jira_raw`, `wiz_raw`, `crowdstrike_raw`,
  `vibranium_raw`) — append-only or upserted snapshots for audit.
- `employees` — synced from internal HR service: `email`, `manager_email`,
  `org_path`, `last_synced_at`.
- `coe_runs` — `id`, `started_at`, `finished_at`, `status`,
  `events_ingested`, `errors_json`.

## Outputs

- **Google Doc**: rendered per-run via Docs API. Sections: new events,
  changed events, events missing owner, events missing SLA, recently
  resolved (optional). Each event row links back to its source record so
  the meeting can dig in when needed.
- **Grafana**: dashboards over `coe_events` — open by severity, by owner,
  by source; SLA breach risk; ownership gaps.

## Critical Files / Modules (to be created)

- `/Users/dmar/src/COE-overview/coe/ingest/` — per-source clients
  (`jira.py`, `wiz.py`, `crowdstrike.py`, `vibranium.py`, `hr.py`)
- `/Users/dmar/src/COE-overview/coe/normalize.py` — severity + owner
  normalization across sources
- `/Users/dmar/src/COE-overview/coe/db/` — Postgres models + migrations
- `/Users/dmar/src/COE-overview/coe/doc/` — Google Doc renderer
- `/Users/dmar/src/COE-overview/deploy/cronjob.yaml` — K8s CronJob spec
- `/Users/dmar/src/COE-overview/grafana/` — dashboard JSON checked in

## Phases

<!-- START_PHASE_1 -->
### Phase 1: Project scaffolding + DB schema (Infrastructure)

**Goal:** Stand up the Python project structure and Postgres schema so all
subsequent phases have a place to put code and a database to write to.

**Scope:**
- Python project setup: `pyproject.toml`, src layout under `coe/`, ruff,
  mypy, pytest, pre-commit hooks.
- Postgres schema via Alembic migrations: `coe_events`, `coe_runs`,
  `employees`, `jira_raw`, `wiz_raw`, `crowdstrike_raw`, `vibranium_raw`.
- Enum: `coe_severity` (CRITICAL, HIGH, UNKNOWN).
- Local dev: `docker-compose.yml` running Postgres on a known port.
- Smoke: a test that runs migrations against the dockerized Postgres.

**Done when:**
- `alembic upgrade head` succeeds against a fresh Postgres.
- `pytest` collects (empty pass acceptable).
- `ruff check` and `mypy` pass on the scaffold.

**Verifies:** None (infrastructure; see AC5).
<!-- END_PHASE_1 -->

<!-- START_PHASE_2 -->
### Phase 2: Source ingest clients (Functionality)

**Goal:** Read-only clients for each external source, each returning typed
records updated since a given timestamp, with structured error types for
auth and transient failures.

**Scope:**
- `coe/ingest/jira.py` — Jira REST v3, JQL filter on COE projects.
- `coe/ingest/wiz.py` — Wiz API for High/Critical findings since.
- `coe/ingest/crowdstrike.py` — Falcon Detects API for High/Critical
  detections since.
- `coe/ingest/vibranium.py` — internal REST API per internal docs.
- `coe/ingest/hr.py` — internal HR service API for the employee dump.
- Shared `coe/ingest/errors.py` — `AuthError`, `TransientError`.

**Done when:**
- Each client has tests against recorded fixtures.
- AC1.1–AC1.5 covered by tests.

**Verifies:** AC1.1, AC1.2, AC1.3, AC1.4, AC1.5.
<!-- END_PHASE_2 -->

<!-- START_PHASE_3 -->
### Phase 3: Normalization layer (Functionality)

**Goal:** Convert raw per-source records into a unified `CoeEvent` shape
with normalized severity and resolved manager.

**Scope:**
- `coe/normalize.py` — severity mapping tables per source; pure
  `to_coe_event(raw, source) -> CoeEvent` functions per source.
- `coe/owner_resolver.py` — given an `owner_email`, look up `manager_email`
  from the `employees` table.

**Done when:**
- AC2.1–AC2.4 covered by unit tests.

**Verifies:** AC2.1, AC2.2, AC2.3, AC2.4.
<!-- END_PHASE_3 -->

<!-- START_PHASE_4 -->
### Phase 4: Ingest pipeline orchestrator (Functionality)

**Goal:** Compose source clients + normalization + DB writes into a single
end-to-end pipeline with delta logic, idempotent upserts, partial-failure
tolerance, and bootstrap handling.

**Scope:**
- `coe/pipeline.py` — reads `last_successful_run_at`, dispatches clients in
  parallel, normalizes results, upserts to `coe_events`, writes a
  `coe_runs` row.
- `coe/db/upsert.py` — idempotent upsert helper using
  `INSERT ... ON CONFLICT`.
- Bootstrap mode (first run) ingests current open backlog.
- Per-source failure isolation.

**Done when:**
- Integration tests against a test Postgres (testcontainers) cover
  AC3.1–AC3.5.

**Verifies:** AC3.1, AC3.2, AC3.3, AC3.4, AC3.5.
<!-- END_PHASE_4 -->

<!-- START_PHASE_5 -->
### Phase 5: Google Doc renderer (Functionality)

**Goal:** Generate the Monday prep Google Doc from current Postgres state,
with the prescribed sections and source-record links.

**Scope:**
- `coe/doc/renderer.py` — query `coe_events` filtered by delta since last
  run, group into sections (new / changed / missing owner / missing SLA /
  recently resolved).
- `coe/doc/google_docs.py` — Docs API client (Google service account +
  Drive scope), creates a new doc per run in a configured Drive folder.

**Done when:**
- Tests cover AC4.1–AC4.4 (Docs API mocked at the SDK boundary).

**Verifies:** AC4.1, AC4.2, AC4.3, AC4.4.
<!-- END_PHASE_5 -->

<!-- START_PHASE_6 -->
### Phase 6: Grafana dashboards (Infrastructure)

**Goal:** Checked-in JSON dashboards backed by the same Postgres source of
truth.

**Scope:**
- `grafana/coe-overview.json` — panels for: open by severity, by owner, by
  source; SLA breach risk; ownership gaps.
- `grafana/README.md` — how to import + configure datasource.

**Done when:**
- Dashboard JSON imports cleanly into a Grafana with the Postgres
  datasource configured and panels return non-error queries.

**Verifies:** None (infrastructure; see AC6).
<!-- END_PHASE_6 -->

<!-- START_PHASE_7 -->
### Phase 7: K8s CronJob deployment (Infrastructure)

**Goal:** Production-ready container + CronJob manifest.

**Scope:**
- `Dockerfile` (multi-stage, slim runtime).
- `deploy/cronjob.yaml` — CronJob with schedule (TBD with meeting owner),
  service account, secret refs.
- `deploy/secret.example.yaml` — secret pattern documentation (no real
  secrets).
- `.github/workflows/build.yaml` — image build on push.

**Done when:**
- Image builds in CI.
- Manifest applies cleanly to a test namespace.
- A manually-triggered job produces a fresh `coe_runs` row.

**Verifies:** None (infrastructure; see AC7).
<!-- END_PHASE_7 -->

## Open Risks / Unknowns (call out before kickoff)

1. **Severity normalization.** CrowdStrike, Wiz, Jira, and Vibranium use
   different severity scales. The mapping is a policy decision, not a
   technical one — needs sign-off from whoever owns COE scope.
2. **Internal HR service contract.** Need to confirm the API exists, its
   auth model, rate limits, and whether it exposes manager chains or just
   direct manager. Blocks the ownership-resolution piece.
3. **Vibranium endpoints.** "REST + token, internal docs available" — need
   the actual endpoint list and pagination behavior before estimating
   ingest time.
4. **First-run bootstrap.** Delta-since-last-run requires a baseline.
   First run should ingest the current open backlog with a synthetic
   `last_successful_run_at` and a one-time backfill flag.
5. **Secrets management.** Five service credentials (Jira, Wiz, CrowdStrike,
   Vibranium, internal HR, Google service account) — plan to use whatever
   the existing K8s secret pattern is, not invent a new one. (Jira is
   read-only now, so a scoped read token is sufficient.)

## Verification

End-to-end smoke once Phase 1 lands:

1. Deploy CronJob to a staging namespace with read-only source tokens.
2. Trigger manually: `kubectl create job --from=cronjob/coe-ingest coe-ingest-manual-1`.
3. Confirm `coe_runs` row written, `coe_events` populated, no errors in logs.
4. Confirm Grafana dashboards render against the fresh data.
5. Render a Google Doc for the test run; spot-check 5 events end-to-end
   (source → `coe_events` row → doc row → resolvable links).
6. Manually change priority/owner on one event in its source system, wait
   for the next run, and confirm the new state shows up in Postgres,
   Grafana, and the next doc.

## Out of Scope (v1)

- Automated owner *inference* (assigning owners to unowned events without a
  human) — only the resolution/escalation chain is auto-pulled.
- Any writeback to Jira or other source systems. Matt actuates by hand
  after the meeting; the system only reads.
- Replacing the existing meeting; this augments it.
