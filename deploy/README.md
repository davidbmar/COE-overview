# COE Overview Kubernetes Deployment

This directory contains the Kubernetes manifests for running the COE Overview ingest + render pipeline on a schedule.

## Quick Start

Follow these steps in order:

### 1. Create Namespace

```bash
kubectl create namespace coe
```

### 2. Swap Image Repository Placeholder

The manifests use a placeholder image repository: `ghcr.io/REPLACE_WITH_OWNER/coe-overview`. You must replace this with your actual GHCR namespace before applying.

Edit `deploy/cronjob.yaml` and replace `REPLACE_WITH_OWNER` with your GitHub organization or username. This placeholder appears in three places (migrate, ingest, and render containers):

```bash
sed -i '' 's|REPLACE_WITH_OWNER|myorg|g' deploy/cronjob.yaml
```

Alternatively, use Kustomize or Helm to handle this substitution in your deployment pipeline.

### 3. Create Secrets

```bash
# Copy the example secret template
cp deploy/secret.example.yaml deploy/secret.yaml

# Edit deploy/secret.yaml with your actual values
# - DATABASE_URL: connection string to your managed Postgres
# - JIRA_USER_EMAIL, JIRA_API_TOKEN: from https://id.atlassian.com/manage-profile/security/api-tokens
# - WIZ_CLIENT_ID, WIZ_CLIENT_SECRET: from Wiz console
# - CROWDSTRIKE_CLIENT_ID, CROWDSTRIKE_CLIENT_SECRET: from CrowdStrike Falcon console
# - VIBRANIUM_API_TOKEN: internal to Capsule
# - HR_API_TOKEN: internal to Capsule
# - service-account.json: Google service account JSON key (see below)

kubectl apply -n coe -f deploy/secret.yaml
```

**Important:** `secret.yaml` is gitignored. Never commit it.

### 4. Apply ConfigMap

```bash
kubectl apply -n coe -f deploy/configmap.yaml
```

You may customize the `JIRA_PROJECTS` and other environment variables by editing `deploy/configmap.yaml` before applying.

### 5. Apply CronJob

```bash
kubectl apply -n coe -f deploy/cronjob.yaml
```

## Google Service Account Setup

The ingest and render stages require a Google service account with access to your Google Drive folder:

1. **Create a service account:**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Select your project
   - Navigate to **Service Accounts** (under IAM & Admin)
   - Click **Create Service Account**
   - Fill in name and description (e.g., "coe-overview")
   - Click **Create and Continue**

2. **Grant Drive permissions:**
   - Skip the optional roles on this page
   - Go to the **Keys** tab
   - Click **Add Key** → **Create new key** → **JSON**
   - A JSON key file will be downloaded
   - In your Google Drive, share the target folder with the service account email (look for the `client_email` field in the JSON)
   - Grant **Editor** permissions

3. **Add to secret:**
   - Copy the entire JSON key file content.
   - Paste it into the `service-account.json` field of the **second** `Secret` (`coe-google-sa`) in `deploy/secret.yaml` — NOT into `coe-secrets`. The two-Secret split keeps the SA JSON file-mounted at `/var/run/secrets/google/service-account.json` while the API tokens remain envFrom-injected.
   - Use a YAML block scalar (`|`) so newlines in the JSON are preserved verbatim, as shown in `secret.example.yaml`.

## Jira API Token

Create a Jira API token at: https://id.atlassian.com/manage-profile/security/api-tokens

Save the token and set it in `deploy/secret.yaml`:
- `JIRA_USER_EMAIL`: your Jira email address
- `JIRA_API_TOKEN`: the API token you just created

## Wiz Configuration

For Wiz client_id and client_secret, refer to the Wiz documentation and Falcon console. These are environment-specific and will be provided to you by your Wiz admin.

## CrowdStrike Configuration

For CrowdStrike client_id and client_secret, refer to the CrowdStrike Falcon console. These are environment-specific and will be provided to you by your CrowdStrike admin.

## Smoke Test

After applying the manifests, manually trigger the CronJob to verify everything works:

```bash
# Trigger a one-off job from the CronJob
kubectl create job -n coe \
  --from=cronjob/coe-overview \
  "coe-manual-$(date +%s)"

# Watch the job run
kubectl logs -n coe -l app=coe-overview --all-containers -f --tail=100

# Once complete, verify a coe_runs row was inserted
psql "$DATABASE_URL" -c "SELECT id, status, events_ingested, doc_url FROM coe_runs ORDER BY id DESC LIMIT 1;"
```

Expected output:
- All three containers (migrate, ingest, render) should exit 0
- A new row in `coe_runs` with the status and doc_url populated
- A new Google Doc in your configured folder (if SA + Drive folder are set up correctly)

## Troubleshooting

### CronJob pod failed
Check the logs of the failed containers:

```bash
# List failed pods
kubectl get pods -n coe --field-selector=status.phase=Failed

# Inspect the failed job
kubectl describe job -n coe <job-name>

# Check logs from all containers (including initContainers)
kubectl logs -n coe <pod-name> --all-containers
# For a previous run (if pod is still there)
kubectl logs -n coe <pod-name> --all-containers --previous
```

### Database connection refused
Verify `DATABASE_URL` in the secret points to a reachable Postgres instance. The CronJob expects a managed Postgres, not the local docker-compose instance.

### Google Drive folder not updated
Check that:
1. The service account email has **Editor** access to the folder
2. `GOOGLE_DRIVE_FOLDER_ID` is correct in `configmap.yaml`
3. `GOOGLE_SERVICE_ACCOUNT_FILE` points to the correct secret mount path (`/var/run/secrets/google/service-account.json`)

### Image pull errors
Ensure:
1. The image repository placeholder was replaced with your actual GHCR namespace
2. Your cluster has credentials to pull from GHCR. For a private GHCR repo:
   ```bash
   # Create a personal-access token with read:packages scope at
   # https://github.com/settings/tokens, then:
   kubectl create secret docker-registry ghcr-creds \
     --docker-server=ghcr.io \
     --docker-username=<github-username> \
     --docker-password=<PAT> \
     -n coe
   ```
   Then uncomment the `imagePullSecrets:` block in `deploy/cronjob.yaml`
   (it points at `ghcr-creds`).
3. The image has been built and pushed to GHCR (via the CI/CD pipeline on merge to main)

## CronJob Schedule

The default schedule is `0 9 * * 1` (9 AM every Monday in America/New_York timezone).

> **Requires Kubernetes 1.27+** for the `spec.timeZone` field. On older clusters
> the field is silently ignored and the schedule runs in the kube-controller-manager's
> local timezone (typically UTC) — meaning the job would fire 4–5 hours off. If
> you're on an older cluster, remove `timeZone` and convert your intended time
> to UTC in `spec.schedule` directly.

To adjust:
1. Edit the `spec.schedule` field in `deploy/cronjob.yaml`
2. Use standard cron syntax (minute, hour, day-of-month, month, day-of-week)
3. Reapply: `kubectl apply -n coe -f deploy/cronjob.yaml`

Coordinate schedule changes with your COE meeting time.

## Next Steps

- Monitor the CronJob with `kubectl logs -n coe -l app=coe-overview -f`
- Adjust `JIRA_PROJECTS` in `configmap.yaml` to match your COE scope
- Fine-tune resource requests/limits based on your cluster capacity and run duration
- Integrate with your CD pipeline to automate the `sed` and `kubectl apply` steps
