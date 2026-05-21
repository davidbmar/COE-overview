# COE Overview — Test Requirements

Maps every acceptance criterion from `docs/design-plans/2026-05-21-coe-overview.md`
to either an automated test (with file path and test type) or documented
human verification. Used by the test-analyst agent after implementation
to validate coverage.

**AC scope prefix:** `coe-overview.AC{N}.{M}` (full scoped form). Bare
`AC{N}.{M}` shorthand is used throughout the phase files; both refer to
the same criteria.

---

## AC1: Source clients pull deltas — Automated (Phase 2)

| AC | Test Type | Test File | What the test verifies |
|----|-----------|-----------|------------------------|
| AC1.1 (Jira since-filter + pagination) | unit | `tests/ingest/test_jira.py` | Single-page response yields N typed `JiraIssue`s; JQL contains `updated >= since` and the COE project allowlist; two-page response paginated via `nextPageToken`. |
| AC1.2 (Wiz severity filter) | unit | `tests/ingest/test_wiz.py` | GraphQL request body contains `["HIGH", "CRITICAL"]` and the since ISO timestamp; token endpoint hit only once for two calls (cache); pagination via `endCursor`. |
| AC1.2 (CrowdStrike severity filter) | unit | `tests/ingest/test_crowdstrike.py` | FQL filter has `max_severity:>=70` + since; offset pagination across 1500 IDs; severity_name buckets correctly (≥90 CRITICAL, ≥70 HIGH). |
| AC1.2 (Vibranium severity filter) | unit | `tests/ingest/test_vibranium.py` | Per-internal-docs endpoint hit with severity + since filters; pagination works. **Test fidelity depends on Vibranium API docs being confirmed first** (see design plan Open Risks #3). |
| AC1.3 (Internal HR employee dump) | unit | `tests/ingest/test_hr.py` | Returns active employees with `email`, `manager_email`, `org_path`; inactive employees filtered out. |
| AC1.4 (AuthError on 401/403) | unit | `tests/ingest/test_base.py`, plus one assertion per source client test | Mocked 401 on any source raises `AuthError(source_name, ...)`; 403 likewise. |
| AC1.5 (TransientError on retry-exhausted 5xx) | unit | `tests/ingest/test_base.py` | 3x 500 then 200 succeeds; max_retries+1 500s raises `TransientError(last_status=500, retries_attempted=max_retries)`; 429 with `Retry-After: 1` sleeps ≥1s before retry (clock asserted via monkeypatched `asyncio.sleep`); transport error followed by 200 retries and succeeds. |

**No human verification required for AC1.** Each source's HTTP shape is fully mockable via `respx`.

---

## AC2: Normalization — Automated (Phase 3)

| AC | Test Type | Test File | What the test verifies |
|----|-----------|-----------|------------------------|
| AC2.1 (severity maps to coe_severity enum) | unit | `tests/test_normalize.py` | Parameterized over every `Source × native_value → CoeSeverity` pair in `SEVERITY_MAP`. |
| AC2.1 (`*_to_coe_event` shape) | unit | `tests/test_normalize.py` | Each of `jira_to_coe_event`, `wiz_to_coe_event`, `crowdstrike_to_coe_event`, `vibranium_to_coe_event` produces a `CoeEvent` with correct `source`, `source_id`, `severity`, `title`, `opened_at`; `raw` round-trips to `.model_dump()`. |
| AC2.2 (unmapped severity → UNKNOWN + warning) | unit | `tests/test_normalize.py` | `normalize_severity(Source.JIRA, "Low")` returns `UNKNOWN`; warning captured via `structlog.testing.capture_logs` contains `source` and `raw_value`. |
| AC2.3 (owner_email → manager_email) | unit | `tests/test_owner_resolver.py` | Resolver constructed with `{"alice@x.com": "manager@x.com"}` resolving `"Alice@X.com"` returns `manager_email="manager@x.com"`, `missing_owner_in_hr=False` (case-insensitive). |
| AC2.4 (missing owner flagged) | unit | `tests/test_owner_resolver.py` | Unknown owner → `manager_email=None`, `missing_owner_in_hr=True`; `None` owner email → all-None with `missing_owner_in_hr=False`. |
| AC2.3 (DB-backed loader) | integration | `tests/db/test_owner_resolver_db.py` | `load_resolver(session)` against a seeded `employees` table returns a resolver that resolves correctly. |

**Human verification — Vibranium severity values:** Before Phase 3 merges, a human must confirm that `SEVERITY_MAP[Source.VIBRANIUM]` keys match the actual string values returned by the Vibranium API (per the BLOCKER FOR PHASE 3 MERGE callout in Phase 3 Task 1).

---

## AC3: Ingest pipeline — Automated (Phase 4)

| AC | Test Type | Test File | What the test verifies |
|----|-----------|-----------|------------------------|
| AC3.1 (since = prior run's finished_at) | integration | `tests/test_pipeline.py` | With a prior `coe_runs` row at T1, source clients are called with `since=T1`. |
| AC3.1 (prior-run helper correctness) | integration | `tests/db/test_runs.py` | `find_prior_run_finished_at` returns the most-recent ok/partial finished_at; failed runs ignored; empty table returns None. |
| AC3.2 (idempotent upserts) | integration | `tests/db/test_upsert.py` | Upserting the same 3 events twice → 3 rows; second pass updates `last_seen_at` but not `opened_at`. |
| AC3.2 (end-to-end idempotency) | integration | `tests/test_pipeline.py` | Two consecutive `run()` calls with the same mocked HTTP responses leave `coe_events` row count unchanged. |
| AC3.3 (success row written) | integration | `tests/test_pipeline.py` and `tests/db/test_runs.py` | After a successful single-source run, the `coe_runs` row has `status='ok'`, `finished_at` set, `events_ingested` correct. |
| AC3.4 (per-source failure isolation) | integration | `tests/test_pipeline.py` | Wiz returns 401, other four sources succeed → `status='partial'`, `errors_json["wiz"]` populated, other sources still ingest (verify their raw tables AND `coe_events` rows). Wiz returns repeated 503 → `errors_json["wiz"]` has the TransientError message, others ingest. |
| AC3.5 (bootstrap mode) | integration | `tests/test_pipeline.py` and `tests/db/test_runs.py` | Empty `coe_runs` table → `is_bootstrap=True`, `since ≈ now() - bootstrap_lookback_days`, full backlog ingested. |
| Raw audit tables populated | integration | `tests/test_pipeline.py` | After a successful run, each of `jira_raw`, `wiz_raw`, `crowdstrike_raw`, `vibranium_raw` has rows matching the mocked HTTP responses. |

**Human verification — AC3.4 partial-failure semantics in production:** Mock-driven tests cover the contract. After first deploy, manually verify by revoking one source's token, watching the next run, and confirming the doc + Grafana show that source's gap with the others intact.

---

## AC4: Google Doc rendering — Automated (Phase 5)

| AC | Test Type | Test File | What the test verifies |
|----|-----------|-----------|------------------------|
| AC4.1 (sections present in doc) | integration | `tests/doc/test_renderer_e2e.py` | Mocked `batchUpdate` body contains the 5 section headings AND one row per seeded event. Returned URL matches `https://docs.google.com/document/d/<id>/edit`. |
| AC4.1 (section bucketing logic) | integration | `tests/doc/test_sections.py` | Seeded events covering each bucket; `build_sections` returns the expected partition; sum of section lengths equals input count; sort order is severity then updated_at DESC. |
| AC4.2 (source-record links) | integration | `tests/doc/test_renderer_e2e.py` | For each seeded event, exactly one `updateTextStyle` request has `link.url` matching the per-source URL pattern (Jira `/browse/{id}`, Wiz `/issues/{id}`, CrowdStrike `/activity/detections/detail/{id}`, Vibranium `/incidents/{id}`). |
| AC4.3 (missing-owner section) | integration | `tests/doc/test_sections.py`, `tests/doc/test_renderer_e2e.py` | Event with `owner_email=None` and `status='open'` lands in `missing_owner` not `new`, even if `opened_at > since`. In the rendered doc, the event appears under the "Events missing owner" heading. |
| AC4.4 (Docs API failure no DB corruption) | integration | `tests/doc/test_renderer_e2e.py` | Mocked 500 on `batchUpdate` raises `GoogleDocsError(500, ...)`; after the failure `coe_runs.doc_url` is unchanged (None) and `coe_events` is unchanged. |
| AC4.4 (Docs API client error translation) | unit | `tests/doc/test_google_docs.py` | Mocked 403 on `files.create` raises `GoogleDocsError(403, ...)`; mocked 500 on `batchUpdate` raises `GoogleDocsError(500, ...)`. |

**Human verification — AC4.1 visual correctness:** After first successful production run, open the generated Google Doc and confirm visually that section headings, ordering, and link styling match the meeting's needs. Automated tests verify the API call shape but not the rendered visual output.

---

## AC5: Project scaffolding + DB schema (Infrastructure)

**Verifies: None.** Operational verification only.

| Check | How verified | Where |
|-------|--------------|-------|
| `uv sync` succeeds | Manual run | Phase 1 Task 1 Step 6 |
| `alembic upgrade head` applies cleanly | Manual run | Phase 1 Task 6 Step 3 |
| `pytest` collects with no errors | Manual run | Phase 1 Task 2 Step 2 |
| `ruff check` and `mypy` pass on scaffold | Manual run | Phase 1 Task 2 Step 2 |
| `docker compose up postgres` produces healthy container | Manual run | Phase 1 Task 4 Step 3 |
| Schema smoke test passes | Automated (integration) | `tests/test_schema_smoke.py` (Phase 1 Task 7) |

**Human verification:** A fresh engineer following Phase 1's tasks lands on a working scaffold with no errors.

---

## AC6: Grafana dashboards (Infrastructure)

**Verifies: None.** Operational verification only.

| Check | How verified | Where |
|-------|--------------|-------|
| Dashboard JSON imports cleanly into Grafana 13+ | Manual run | Phase 6 Task 1 Verification |
| Every panel renders without errors against the COE Postgres | Manual run | Phase 6 Task 1 Verification |
| `$datasource` template variable lets the user pick at import | Manual run | Phase 6 Task 1 Step 3 |

**Human verification — dashboard usefulness:** After the dashboard imports cleanly, a human reviews each panel for actionable signal during the meeting. Adjust panels based on meeting feedback over the first few weeks.

---

## AC7: K8s CronJob deployment (Infrastructure)

**Verifies: None.** Operational verification only.

| Check | How verified | Where |
|-------|--------------|-------|
| Multi-stage Docker image builds | Manual run | Phase 7 Task 1 Verification |
| Image runs as uid 1000 | Manual run | Phase 7 Task 1 Verification |
| GitHub Actions workflow pushes image to GHCR on merge to main | CI run | Phase 7 Task 2 Verification |
| `kubectl apply --dry-run=client` passes on all manifests | Manual run | Phase 7 Task 3 Verification |
| Manually-triggered job produces fresh `coe_runs` row in target Postgres | Manual run | Phase 7 Task 3 Verification |
| `kubectl apply` order documented in `deploy/README.md` produces working CronJob | Manual run | Phase 7 Task 4 Verification |

**Human verification — production schedule:**
- Confirm `schedule: "0 9 * * 1"` matches the actual COE meeting time (lock in with meeting owner).
- After first successful run, confirm the rendered Google Doc lands in the configured Drive folder with the SA having appropriate sharing.
- Verify the next Monday's run picks up Matt's manual Jira/source-system updates from the previous week (the closed-loop check).

---

## Summary

| Category | Count |
|----------|-------|
| AC cases with at least one automated test | 16 (AC1.1–AC1.5, AC2.1–AC2.4, AC3.1–AC3.5, AC4.1–AC4.4) |
| AC groups with explicit "Verifies: None" (infrastructure) | 3 (AC5, AC6, AC7) |
| Human verification items | 8 (Vibranium severity, AC3.4 production check, AC4.1 visual, scaffold smoke, dashboard usefulness, schedule, doc folder, closed-loop check) |
| Test files referenced | 14 |

**No AC is left without coverage.** Every functionality AC maps to at least one automated test in a specific file. Infrastructure ACs map to documented operational verification steps in their phase plans.
