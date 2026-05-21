# COE Overview — Phase 6: Grafana Dashboards

**Goal:** A single checked-in Grafana dashboard backed by the same Postgres
source of truth, showing open events by severity / owner / source, SLA
breach risk, and ownership gaps. The dashboard JSON lives in the repo and
can be imported into any Grafana 13+ with a Postgres datasource
configured.

**Architecture:** One dashboard JSON, one `$datasource` template variable
so panels resolve against the user's chosen Postgres datasource (no
hardcoded UID). A README documents the import path. No Python code in
this phase.

**Tech Stack:** Grafana 13.0.1+ (current stable May 2026, schemaVersion 42),
Postgres datasource.

**Scope:** Phase 6 of 7. Infrastructure. No tests in the codebase sense —
verification is operational (import the JSON, see panels render).

**Codebase verified:** 2026-05-21 — `coe_events` and `coe_runs` schemas
landed in Phase 1 (the `(source, source_id)` unique constraint is
declared directly in Phase 1 Task 6). `grafana/` does not exist.

---

## Acceptance Criteria Coverage

**Verifies: None.** Per the design plan's AC6, this is infrastructure
only. Operational verification: importing the checked-in JSON into a
Grafana 13+ instance with a Postgres datasource configured produces
panels that successfully query the COE tables and render non-empty (or
empty-but-error-free) results.

---

## External dependency findings (2026)

- **Schema version:** 42 (Grafana 13.0.1, May 2026).
- **Checked-in JSON shape:** bare dashboard object (no `{"dashboard":
  {...}}` wrapper, no `id`/`iteration`/`overwrite` fields, `"version": 0`).
- **Datasource template variable:** declare `$datasource` of type
  `datasource` with `datasourceType: "postgres"` so the dashboard binds
  to whatever Postgres datasource the user picks at import time.
- **Postgres panel queries:** raw SQL using the `$__timeFilter(col)` and
  `$__timeGroupAlias(col, '1d')` macros for time-bounded queries.
- **Import path:** POST to `/api/dashboards/db` with
  `{"dashboard": <json>, "overwrite": true}`. Or Git Sync (GA in v13) for
  a more declarative path.

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->

<!-- START_TASK_1 -->
### Task 1: Author the dashboard JSON

**Files:**
- Create: `/Users/dmar/src/COE-overview/grafana/coe-overview.json`

**Implementation:**

Single dashboard with the panels below. Build it once in a local
Grafana 13 against the docker-compose Postgres, then export → strip
metadata → check in.

**Panels (in this order):**

1. **Stat: Open events (count)** — total open events (excluding
   `coe_review_status = 'resolved'`).
   ```sql
   SELECT count(*) AS value
   FROM coe_events
   WHERE coe_review_status IS DISTINCT FROM 'resolved'
   ```

2. **Stat: Open Critical** — same with `severity = 'CRITICAL'`.

3. **Stat: Missing owner** — open events with `owner_email IS NULL`.

4. **Stat: SLA breaches pending** — open events with `sla_due_at < now()`.

5. **Pie: Open by severity** — group open events by `severity`.

6. **Bar gauge: Open by source** — group by `source`.

7. **Table: Top owners by open count** — group by `owner_email`, count
   open events, order desc, top 20.

8. **Table: SLA breach risk (within 7 days)** — open events where
   `sla_due_at` is in the next 7 days. Columns: severity, title, owner,
   `sla_due_at`, source link.

9. **Time series: Ingested events per run (last 12 weeks)** —
   `coe_runs.events_ingested` vs `started_at`, using
   `$__timeFilter(started_at)`.

10. **Time series: Errors per run (last 12 weeks)** — count of distinct
    sources with errors per run from `coe_runs.errors_json` (use
    `jsonb_array_length`/`jsonb_object_keys` as appropriate).

**Templating:**

```json
"templating": {
  "list": [
    {
      "name": "datasource",
      "label": "Postgres datasource",
      "type": "datasource",
      "query": "postgres",
      "current": {"selected": false, "text": "", "value": ""},
      "hide": 0,
      "refresh": 1
    }
  ]
}
```

Each panel's `"datasource"` field: `{"type": "postgres", "uid":
"${datasource}"}`.

**Dashboard JSON skeleton** (shape, not full content — author panels
in-tool then export):

```json
{
  "uid": "coe-overview",
  "title": "COE Overview",
  "schemaVersion": 42,
  "version": 0,
  "timezone": "browser",
  "refresh": "5m",
  "time": {"from": "now-12w", "to": "now"},
  "templating": {"list": [...]},
  "panels": [...],
  "annotations": {"list": []},
  "tags": ["coe", "security"]
}
```

**Authoring steps:**

1. Start Grafana locally:
   ```bash
   docker run -d --name coe-grafana -p 3000:3000 \
     -e GF_SECURITY_ADMIN_PASSWORD=admin grafana/grafana:13.0.1
   ```
2. In the UI, add a Postgres datasource pointing at the docker-compose
   Postgres (`host.docker.internal:5432`, db `coe`, user `coe`, password
   `coe`).
3. Build the 10 panels in the UI against that datasource. Use
   `${datasource}` as the datasource UID via the template variable so
   panels resolve dynamically.
4. Export the dashboard JSON (Share → Export → "Save to file").
5. **Strip metadata before checking in:** open the exported file and
   remove `id`, `iteration`, `__inputs`, `__requires`, `__elements`,
   and any `overwrite` field. Set `"version": 0`.
6. Save the cleaned JSON to `grafana/coe-overview.json`.
7. Re-import the cleaned JSON to confirm it still works.

**Verification:**

Manual: import the cleaned JSON into a fresh Grafana, point it at the
COE Postgres, confirm each panel renders without errors. Empty results
are acceptable (e.g., on a freshly-bootstrapped DB with no events yet) —
the test is that the SQL parses and the panel renders an empty state
rather than an error toast.

**Commit:** `feat(grafana): COE overview dashboard`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Grafana import README

**Files:**
- Create: `/Users/dmar/src/COE-overview/grafana/README.md`

**Implementation:**

Document two import paths:

**Manual (one-shot):**
```bash
# 1. Configure a Postgres datasource in Grafana pointing at the COE DB.
# 2. POST the dashboard JSON to the Grafana API:
curl -X POST "$GRAFANA_URL/api/dashboards/db" \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq '{dashboard: ., overwrite: true}' grafana/coe-overview.json)"
```

**Git Sync (declarative, GA in Grafana 13):**

> Configure Git Sync in the Grafana UI (Admin → Provisioning → Git Sync)
> pointing at this repo's `grafana/` folder. Grafana 13+ will keep its
> dashboards in lockstep with the checked-in JSON.

Also document:
- The `$datasource` template variable — pick the Postgres datasource at
  import time; no UID is hardcoded.
- Required Postgres permissions on the datasource user: `SELECT` on
  `coe_events`, `coe_runs`, `employees`.

**Verification:**

Manual: a fresh engineer follows the README and ends up with a working
dashboard.

**Commit:** `docs(grafana): import + datasource README`
<!-- END_TASK_2 -->

<!-- END_SUBCOMPONENT_A -->

---

## Done When

- [ ] `grafana/coe-overview.json` exists, schemaVersion 42, with the 10
  panels listed above and the `$datasource` template variable.
- [ ] `grafana/README.md` documents the import path and the datasource
  user's required permissions.
- [ ] A fresh Grafana 13+ import against the COE Postgres renders every
  panel without errors.
- [ ] Branch pushed.

## Notes for Subsequent Phases

- Phase 7's CronJob doesn't touch Grafana — the dashboard is a passive
  view over the Postgres that Phase 4 maintains.
