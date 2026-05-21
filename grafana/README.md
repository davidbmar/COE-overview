# Grafana COE Overview Dashboard

The COE Overview dashboard provides a single, unified view of all open security events, SLA breach risk, and ownership gaps across your security tools. The dashboard is defined in checked-in JSON (`coe-overview.json`) and can be imported into any Grafana 13+ instance with a Postgres datasource configured.

## Dashboard Contents

The dashboard includes 10 panels:

1. **Open events (count)** — Total count of open (unresolved) events across all sources
2. **Open Critical** — Count of open events with `severity = CRITICAL`
3. **Missing owner** — Count of open events with no assigned owner
4. **SLA breaches pending** — Count of open events past their SLA due date
5. **Open by severity** — Pie chart breakdown of open events by severity level
6. **Open by source** — Bar gauge showing open event count by integration source
7. **Top owners by open count** — Table of the top 20 owners by open event count
8. **SLA breach risk (within 7 days)** — Table of events at risk of SLA breach in the next 7 days
9. **Ingested events per run (last 12 weeks)** — Time series of event ingestion volume per COE run
10. **Errors per run (last 12 weeks)** — Time series of error count per COE run

All panels query the COE Postgres database directly. The time range defaults to the last 12 weeks.

## Import Methods

### Option 1: Manual One-Shot Import (curl)

Use this method to import the dashboard into a single Grafana instance.

**Prerequisites:**

1. Ensure a Postgres datasource is already configured in Grafana and pointing to the COE database.
2. Have the Grafana API token (or admin user credentials) available.
3. Know your Grafana URL (e.g., `https://grafana.example.com`).

**Steps:**

```bash
# Set environment variables
export GRAFANA_URL="https://grafana.example.com"
export GRAFANA_TOKEN="glsa_your_api_token_here"

# Clone or fetch the repo
git clone https://github.com/your-org/COE-overview.git
cd COE-overview

# Import the dashboard
curl -X POST "$GRAFANA_URL/api/dashboards/db" \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq '{dashboard: ., overwrite: true}' grafana/coe-overview.json)"
```

If the import is successful, the API will return JSON with the dashboard's new UID and version. An HTTP 200 response indicates success.

### Option 2: Git Sync (Declarative, Recommended for Fleets)

Use this method to keep Grafana dashboards in lockstep with the repository. Git Sync is GA in Grafana 13+.

**Steps:**

1. In the Grafana UI, go to **Admin → Provisioning → Git Sync**.
2. Click **Add datasource folder** (or **Configure** if one already exists).
3. Enter:
   - **Repository URL:** `https://github.com/your-org/COE-overview.git`
   - **Reference:** `main` (or your default branch)
   - **Subfolder:** `grafana/`
   - **Commit author (email):** `grafana@example.com`
4. Click **Test** to verify the configuration.
5. Grafana will now automatically sync and provision the dashboard whenever the repo is updated.

Once configured, the dashboard will be automatically imported and kept up-to-date. Grafana will also create a Git sync status page showing sync logs and any conflicts.

## Datasource Configuration

### The `$datasource` Template Variable

The dashboard uses a **template variable** named `$datasource` to allow you to select which Postgres datasource to query at import or view time. This means:

- **No hardcoded UIDs:** The dashboard JSON contains no datasource UID — it's always `${datasource}`.
- **Pick at import time:** When you import, Grafana will prompt you to select a Postgres datasource to bind to the dashboard.
- **Switch after import:** Once imported, you can click the datasource dropdown at the top of the dashboard to switch between Postgres datasources without re-importing.

### Required Postgres Permissions

The Postgres datasource user must have `SELECT` permission on the tables the dashboard queries.

Today's panels only read `coe_events` and `coe_runs`. The `employees` grant below is kept for anticipated panels (top managers by open count, etc.) — drop it if you want strict least-privilege.

The COE schema lives in the default `public` schema (no `coe.` schema prefix):

```sql
-- Grant permissions to the datasource user (e.g., 'grafana')
GRANT SELECT ON coe_events TO grafana;
GRANT SELECT ON coe_runs TO grafana;
GRANT SELECT ON employees TO grafana;  -- optional: for future panels
```

If the datasource user does not have these permissions, panels will fail to render with a "permission denied" error.

## Pre-Flight Checklist

Before importing the dashboard, ensure the following:

1. **Migrations applied:** The COE database schema must be up to date.
   ```bash
   cd /path/to/COE-overview
   alembic upgrade head
   ```

2. **At least one COE run ingested:** For the time series panels (Ingested events per run, Errors per run) to show data, the `coe_runs` and `coe_events` tables must have at least one entry.
   ```bash
   # Trigger a COE run (example command — adjust to your deployment)
   coe run
   ```

3. **Postgres datasource configured:** In Grafana, Admin → Data Sources, ensure at least one Postgres datasource is set up and pointing to your COE database.

Without these, the dashboard will import successfully but panels may show "No data" or errors.

## Troubleshooting

### Panels show "Plugin error" or "No data"

- **Check Postgres connectivity:** Verify the datasource can connect to the COE database (Admin → Data Sources → Test).
- **Check permissions:** Verify the datasource user has `SELECT` on `coe_events` and `coe_runs` (and `employees`, if you granted it).
- **Check data:** Run the SQL queries manually in `psql` to ensure the tables contain data.

### Import returns a 401 error

- The Grafana API token is invalid or expired. Generate a new token in Admin → API Keys.

### Import returns a 400 error

- The dashboard JSON is malformed. Validate it: `jq . grafana/coe-overview.json` should not error.

### Git Sync does not update the dashboard

- Check the Git Sync logs in Grafana (Admin → Provisioning → Git Sync → Logs).
- Verify the repository URL and credentials (if private) are correct.
- Ensure the branch and subfolder paths are correct.

## Dashboard Updates

To update the dashboard JSON:

1. Make changes to `grafana/coe-overview.json`.
2. Commit and push to the repository.
3. If using Git Sync, Grafana will automatically re-sync and reload the dashboard within a few minutes.
4. If using manual import, re-run the curl command with `"overwrite": true` to replace the existing dashboard.

## See Also

- [Grafana Dashboards Docs](https://grafana.com/docs/grafana/latest/dashboards/)
- [Grafana Postgres Datasource](https://grafana.com/docs/grafana/latest/datasources/postgres/)
- [Git Sync (Grafana 13+)](https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/provisioning/)
