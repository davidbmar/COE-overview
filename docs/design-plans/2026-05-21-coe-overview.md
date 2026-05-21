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
