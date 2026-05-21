# COE Overview — Phase 7: K8s CronJob Deployment

**Goal:** Production-ready container image and Kubernetes CronJob manifest
that runs the ingest pipeline (Phase 4) followed by the Google Doc
renderer (Phase 5) every Monday morning. Image is built and pushed by
GitHub Actions on every merge to `main`.

**Architecture:** Multi-stage Dockerfile using `uv` (build stage installs
deps + project into a venv, runtime stage copies the venv into a slim
Python 3.12 base, runs as non-root). One K8s CronJob with three stages
chained via initContainers: stage 0 runs `alembic upgrade head` (so
schema is current on every run before any data is touched), stage 1
runs `python -m coe` (ingest), and the main container runs `python -m
coe.doc` (render). Each stage must exit 0 before the next starts, so a
failed migration or ingest never produces a stale-data doc. Secrets and
config injected via `envFrom`. Image build is a single GitHub Actions
workflow using `docker/build-push-action@v6` pushing to GHCR. The image
repo in the manifest is a placeholder (`ghcr.io/REPLACE_WITH_OWNER/coe-overview`)
that you swap with your org's GHCR namespace at deploy time
(or via Kustomize / Helm).

**Tech Stack:** Docker (multi-stage), uv, Python 3.12-slim, Kubernetes
`batch/v1` CronJob, GitHub Actions, GHCR.

**Scope:** Phase 7 of 7. Infrastructure. The K8s manifest goes into the
repo; actual cluster deployment is left to the user (the manifest
applies cleanly to any namespace they have admin in).

**Codebase verified:** 2026-05-21 — Phases 1–5 have landed. `coe/`
package, `coe/__main__.py`, and `coe/doc/__main__.py` exist. No
Dockerfile, no `deploy/`, no `.github/workflows/` yet.

---

## Acceptance Criteria Coverage

**Verifies: None.** Per the design plan's AC7, this is infrastructure
only. Operational verification: image builds in CI, manifest applies to
a test namespace, a manually-triggered job produces a fresh `coe_runs`
row in the target Postgres.

---

## External dependency findings (2026)

- **Python base:** `python:3.12-slim` is the current default — smaller
  and faster than 3.11.
- **uv in Docker:** `COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx
  /bin/`. Set `UV_LINK_MODE=copy` (NOT default `hardlink`) to keep the
  venv portable across stages. `UV_COMPILE_BYTECODE=1` to compile at
  build time.
- **GitHub Actions versions (May 2026):** `actions/checkout@v4`,
  `docker/setup-buildx-action@v3`, `docker/login-action@v3`,
  `docker/metadata-action@v5`, `docker/build-push-action@v6`. Always pin
  majors.
- **K8s CronJob:** `batch/v1` (stable since 1.21). `timeZone` field
  works in 1.27+ — use IANA identifiers.
- **Two-stage chaining:** initContainer + main container is the
  preferred 2026 pattern over two separate CronJobs (atomic, simpler,
  auto-cleanup via CronJob TTL).

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->

<!-- START_TASK_1 -->
### Task 1: Multi-stage Dockerfile

**Files:**
- Create: `/Users/dmar/src/COE-overview/Dockerfile`
- Create: `/Users/dmar/src/COE-overview/.dockerignore`

**Implementation:**

**`Dockerfile`:**

```dockerfile
# syntax=docker/dockerfile:1.7

# ------ Build stage ------
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install deps in a separate layer for caching
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-editable

# Install the project itself
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable

# ------ Runtime stage ------
FROM python:3.12-slim

RUN useradd -m -u 1000 coe \
 && mkdir /app \
 && chown coe:coe /app

COPY --from=builder --chown=coe:coe /app/.venv /app/.venv
COPY --from=builder --chown=coe:coe /app/coe /app/coe
COPY --from=builder --chown=coe:coe /app/alembic /app/alembic
COPY --from=builder --chown=coe:coe /app/alembic.ini /app/alembic.ini

USER coe
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

# Default entrypoint is the ingest pipeline; the doc job overrides
# `command` in the K8s manifest.
ENTRYPOINT ["python", "-m", "coe"]
```

**`.dockerignore`:**

```
.git
.venv
.pytest_cache
.mypy_cache
.ruff_cache
__pycache__
*.pyc
tests/
docs/
docker-compose.yml
.env
.env.local
.github/
```

**Verification:**

```bash
cd /Users/dmar/src/COE-overview
docker build -t coe-overview:dev .
# Smoke: container has the venv on PATH and runs as non-root
docker run --rm coe-overview:dev python -c "import coe; print(coe.__name__)"
docker run --rm coe-overview:dev id
```
Expected: `coe` printed; `id` shows uid=1000(coe).

**Commit:** `feat(docker): multi-stage Dockerfile with uv`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: GitHub Actions image build workflow

**Files:**
- Create: `/Users/dmar/src/COE-overview/.github/workflows/build.yaml`

**Implementation:**

```yaml
name: Build and push image

on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:
    branches: [main]

permissions:
  contents: read
  packages: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        if: github.event_name != 'pull_request'
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/${{ github.repository }}
          tags: |
            type=ref,event=branch
            type=ref,event=pr
            type=sha,prefix=sha-
            type=semver,pattern={{version}}
            type=raw,value=latest,enable={{is_default_branch}}

      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64
          push: ${{ github.event_name != 'pull_request' }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

**Verification:**

```bash
git add .github/workflows/build.yaml
git commit -m "ci: build and push image to ghcr"
git push origin coe-overview
# Open your repo's Actions tab (https://github.com/<owner>/<repo>/actions)
# and confirm the workflow ran on the push. On merge to main, confirm the
# image appears under your repo's "Packages" tab.
```

**Commit:** `ci: build and push image to ghcr`
<!-- END_TASK_2 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 3-4) -->

<!-- START_TASK_3 -->
### Task 3: K8s CronJob manifest

**Files:**
- Create: `/Users/dmar/src/COE-overview/deploy/cronjob.yaml`
- Create: `/Users/dmar/src/COE-overview/deploy/configmap.yaml`

**Implementation:**

**`deploy/configmap.yaml`:**

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: coe-config
data:
  BOOTSTRAP_LOOKBACK_DAYS: "90"
  JIRA_BASE_URL: "https://capsule.atlassian.net"
  JIRA_PROJECTS: "SEC,OPS"           # update to match your COE scope
  WIZ_API_URL: "https://api.us.wiz.io/graphql"
  WIZ_AUTH_URL: "https://auth.app.wiz.io/oauth/token"
  CROWDSTRIKE_BASE_URL: "https://api.crowdstrike.com"
  VIBRANIUM_BASE_URL: "https://vibranium.internal.capsule.com"
  HR_BASE_URL: "https://hr-api.internal.capsule.com"
  GOOGLE_DRIVE_FOLDER_ID: "REPLACE_WITH_FOLDER_ID"
```

**`deploy/cronjob.yaml`:**

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: coe-overview
  labels:
    app: coe-overview
spec:
  schedule: "0 9 * * 1"          # 9am Monday
  timeZone: "America/New_York"   # K8s 1.27+
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 600
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 1
      ttlSecondsAfterFinished: 604800   # 7 days
      template:
        metadata:
          labels:
            app: coe-overview
        spec:
          restartPolicy: OnFailure
          # Stage 0: apply migrations before any data is read or written
          initContainers:
            - name: migrate
              image: ghcr.io/REPLACE_WITH_OWNER/coe-overview:latest
              imagePullPolicy: Always
              command: ["alembic", "upgrade", "head"]
              envFrom:
                - configMapRef: { name: coe-config }
                - secretRef:    { name: coe-secrets }
              env:
                - name: DATABASE_URL
                  valueFrom:
                    secretKeyRef: { name: coe-secrets, key: DATABASE_URL }
              resources:
                requests: { cpu: "100m", memory: "128Mi" }
                limits:   { cpu: "500m", memory: "256Mi" }
            # Stage 1: ingest, must succeed before render starts
            - name: ingest
              image: ghcr.io/REPLACE_WITH_OWNER/coe-overview:latest
              imagePullPolicy: Always
              args: []                          # uses Dockerfile ENTRYPOINT
              envFrom:
                - configMapRef: { name: coe-config }
                - secretRef:    { name: coe-secrets }
              env:
                - name: DATABASE_URL
                  valueFrom:
                    secretKeyRef: { name: coe-secrets, key: DATABASE_URL }
                - name: COE_RUN_ID_PATH
                  value: /var/run/coe/last_run_id
              volumeMounts:
                - name: google-sa
                  mountPath: /var/run/secrets/google
                  readOnly: true
                - name: run-handoff
                  mountPath: /var/run/coe
              resources:
                requests: { cpu: "250m", memory: "512Mi" }
                limits:   { cpu: "1",    memory: "1Gi"   }
          # Stage 2: render doc — runs only if migrate AND ingest exited 0
          containers:
            - name: render
              image: ghcr.io/REPLACE_WITH_OWNER/coe-overview:latest
              imagePullPolicy: Always
              command: ["python", "-m", "coe.doc"]
              envFrom:
                - configMapRef: { name: coe-config }
                - secretRef:    { name: coe-secrets }
              env:
                - name: DATABASE_URL
                  valueFrom:
                    secretKeyRef: { name: coe-secrets, key: DATABASE_URL }
                - name: GOOGLE_SERVICE_ACCOUNT_FILE
                  value: /var/run/secrets/google/service-account.json
                - name: COE_RUN_ID_PATH
                  value: /var/run/coe/last_run_id
              volumeMounts:
                - name: google-sa
                  mountPath: /var/run/secrets/google
                  readOnly: true
                - name: run-handoff
                  mountPath: /var/run/coe
                  readOnly: true
              resources:
                requests: { cpu: "100m", memory: "256Mi" }
                limits:   { cpu: "500m", memory: "512Mi" }
          volumes:
            - name: google-sa
              secret:
                secretName: coe-google-sa
                items:
                  - key: service-account.json
                    path: service-account.json
            # Shared emptyDir for run_id handoff between ingest and render.
            # Ingest writes /var/run/coe/last_run_id; render reads it.
            - name: run-handoff
              emptyDir: {}
```

**Verification:**

```bash
# Dry-run validation
kubectl apply --dry-run=client -f deploy/configmap.yaml
kubectl apply --dry-run=client -f deploy/cronjob.yaml

# In a real cluster (test namespace):
kubectl apply -n coe-test -f deploy/configmap.yaml
kubectl apply -n coe-test -f deploy/cronjob.yaml
# Manually trigger a single job
kubectl create job --from=cronjob/coe-overview \
  -n coe-test "coe-manual-$(date +%s)"
# Watch logs
kubectl logs -n coe-test -l app=coe-overview --all-containers --tail=200
# Confirm a coe_runs row was written
psql "$DATABASE_URL" -c "SELECT id, status, events_ingested, doc_url FROM coe_runs ORDER BY id DESC LIMIT 1;"
```

**Commit:** `feat(deploy): k8s cronjob with initContainer for ingest->doc`
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Secret example + documentation

**Files:**
- Create: `/Users/dmar/src/COE-overview/deploy/secret.example.yaml`
- Create: `/Users/dmar/src/COE-overview/deploy/README.md`

**Implementation:**

**`deploy/secret.example.yaml`** (NO real values — for documentation):

```yaml
# Copy this to deploy/secret.yaml, fill values, apply it ONCE per namespace.
# secret.yaml is in .gitignore — never commit real secrets.
apiVersion: v1
kind: Secret
metadata:
  name: coe-secrets
type: Opaque
stringData:
  DATABASE_URL: "postgresql+asyncpg://USER:PASSWORD@HOST:5432/DBNAME"

  JIRA_USER_EMAIL: ""
  JIRA_API_TOKEN: ""

  WIZ_CLIENT_ID: ""
  WIZ_CLIENT_SECRET: ""

  CROWDSTRIKE_CLIENT_ID: ""
  CROWDSTRIKE_CLIENT_SECRET: ""

  VIBRANIUM_API_TOKEN: ""

  HR_API_TOKEN: ""

---
# Google service account JSON is its own Secret, mounted as a file.
apiVersion: v1
kind: Secret
metadata:
  name: coe-google-sa
type: Opaque
stringData:
  service-account.json: |
    {
      "type": "service_account",
      "...": "...full SA JSON..."
    }
```

**`deploy/.gitignore`** (add a line):

```
secret.yaml
```

**`deploy/README.md`**:

Document the apply order:
1. `kubectl create namespace coe`
2. **Swap the image repo placeholder** in `deploy/cronjob.yaml` —
   replace `ghcr.io/REPLACE_WITH_OWNER/coe-overview` with your actual
   GHCR namespace (e.g., `ghcr.io/myorg/coe-overview`). This appears in
   three places (migrate, ingest, render containers). A simple
   `sed -i '' 's|REPLACE_WITH_OWNER|myorg|g' deploy/cronjob.yaml` works.
3. `cp deploy/secret.example.yaml deploy/secret.yaml`, fill values,
   `kubectl apply -n coe -f deploy/secret.yaml`
4. `kubectl apply -n coe -f deploy/configmap.yaml`
5. `kubectl apply -n coe -f deploy/cronjob.yaml`

Document the Google service account setup:
- Create a service account in GCP project `<project>`.
- Grant Drive `editor` on the target Drive folder by sharing the folder
  with the SA's email address.
- Download the SA JSON key and paste into the `coe-google-sa` secret.

Document Jira API token creation: https://id.atlassian.com/manage-profile/security/api-tokens

Document Wiz / CrowdStrike client_id+secret creation as references to
each vendor's docs (intentionally not duplicated here — vendor flows
change).

Document the smoke test (manually triggering the CronJob as in Task 3's
verification).

**Verification:**

```bash
kubectl apply --dry-run=client -f deploy/secret.example.yaml
```
Expected: passes validation.

**Commit:** `docs(deploy): apply order, secret template, smoke test`
<!-- END_TASK_4 -->

<!-- END_SUBCOMPONENT_B -->

---

## Done When

- [ ] `Dockerfile`, `.dockerignore` exist; `docker build .` succeeds and
  the image runs as uid 1000.
- [ ] `.github/workflows/build.yaml` exists; the workflow runs on push
  and pushes an image to GHCR on merge to `main`.
- [ ] `deploy/{cronjob,configmap,secret.example}.yaml` exist and pass
  `kubectl apply --dry-run=client`.
- [ ] `deploy/README.md` documents the apply order and the Google SA
  setup.
- [ ] In a target cluster: `kubectl create job --from=cronjob/coe-overview
  coe-manual-test` produces a fresh `coe_runs` row in the target Postgres
  and (if the SA + Drive folder are configured) a Google Doc in the
  configured folder.
- [ ] Branch pushed and merged to `main`.

## Notes

- **First-run bootstrap:** the first CronJob run will use `is_bootstrap=true`
  and ingest the last 90 days of open backlog (per
  `BOOTSTRAP_LOOKBACK_DAYS`).
- **Schedule tuning:** the `0 9 * * 1` cron is 9am every Monday in the
  configured timezone. Adjust with the meeting owner once the meeting
  time is locked in.
- **Two-stage failure modes:** if the ingest initContainer fails, the
  render container never starts and the CronJob pod fails. Next Monday's
  run starts fresh; investigate logs via
  `kubectl logs -n coe -l app=coe-overview --previous --all-containers`.
- **Database location:** the CronJob expects a managed Postgres (the
  `DATABASE_URL` secret) — the docker-compose Postgres from Phase 1 is
  local-dev only.
