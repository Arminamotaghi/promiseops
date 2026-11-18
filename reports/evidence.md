# Evidence index

Every row is one behavior shown on the real environment. A row counts only when all of its
cells are filled and the raw result is linked. Rows marked `pending` have not been run yet.

## How to record a row

1. Run the command on the real environment (staging, prod, the team database or Airflow).
2. Save the raw output under `reports/evidence/<ID>-<short-name>.txt` or `.json`.
   Deployment records come from the pipeline artifacts `deploy-staging.json`,
   `deploy-prod.json` and `rollback-prod.json`.
3. Remove credentials before committing: API key, database URL, feed key, tokens.
4. Fill the row: source commit, image digest (when a deployed image is involved), model
   version, the company clock (`as_of`) or cohort used, the command, the UTC time and the
   result in one line. A screenshot may be added, never used instead of the raw output.

## Data and quality

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| D1 | Valid batch is committed with its receipt | pending | n/a | n/a | pending | pending |
| D2 | Replaying the same batch changes no row count (`replayed: true`) | pending | n/a | n/a | pending | pending |
| D3 | Broken batch is rejected whole and quarantined | pending | n/a | n/a | pending | pending |
| D4 | Same batch ID with another payload is rejected | pending | n/a | n/a | pending | pending |
| D5 | Interrupted ingestion leaves no partial rows and can be retried | pending | n/a | n/a | pending | pending |

## Model and evaluation

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| M1 | Temporal split and label cutoffs (no future labels in training) | pending | n/a | pending | pending | pending |
| M2 | Baseline and two approaches: N, prevalence, recall@10%, precision@10%, AP, Brier | pending | n/a | pending | pending | pending |
| M3 | Gate decision on identical validation IDs (cohort hash) | pending | n/a | pending | pending | pending |
| M4 | Weaker candidate is rejected by the gate | pending | n/a | pending | pending | pending |

## Orchestration and lifecycle

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| O0 | Airflow worker pre-flight (`promiseops_env_check` after `airflow-sync`): four settings present, gateway, database and MLflow reachable, `promiseops` imports from `team-code` | pending | n/a | n/a | n/a | pending |
| O1 | Ingestion DAG run for a chosen `as_of`; retry of the same run is a no-op | pending | n/a | n/a | pending | pending |
| O2 | Training DAG submits the queued Job (202, Job name, final status) | pending | pending | pending | pending | pending |
| O3 | Lineage in MLflow: artifact SHA256, commit, cutoffs, cohort hash, metrics, gate | pending | n/a | pending | pending | pending |
| O4 | Replay on a later period: predictions recorded first, evaluated after labels mature | pending | pending | pending | pending | pending |

## API and tests

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| A1 | Live contract check: probes, 401 without key, prediction, metrics | pending | pending | pending | pending | pending |
| A2 | Repeated request ID returns the original result; other order gives 409 | pending | pending | pending | pending | pending |
| A3 | Unknown order 404, invalid input 422 | pending | pending | pending | pending | pending |
| A4 | Batch priority (`score-priority`): cohort, scored count and top 10% stored as one `priority_runs` row; a rerun with the same `as_of` returns the same run and adds no row; output shape as `contracts/priority.example.json` | pending | pending | pending | pending | pending |

## Kubernetes and CI

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| K1 | Staging deploy by digest and `verify-staging` | pending | pending | pending | n/a | pending |
| K2 | Production deploy with manual approval (approver recorded) | pending | pending | pending | n/a | pending |
| K3 | Production deploy refused for a user who is not the approver | pending | n/a | n/a | n/a | pending |
| K4 | Release with broken readiness never receives traffic and is reverted | pending | pending | pending | n/a | pending |
| K5 | Rollback to an earlier digest, readiness and one real prediction verified | pending | pending | pending | n/a | pending |
| K6 | Load test result (rate, duration, failures, latency) | pending | pending | pending | pending | pending |
| K7 | HPA scales 2 to 3 under load and back to 2 | pending | pending | pending | n/a | pending |
| K8 | Deleted pod is replaced while the service keeps answering | pending | pending | pending | n/a | pending |
| K9 | Pre-deploy migration Job applies `sql/*.sql` on every deploy; files already applied change nothing | pending | pending | n/a | n/a | pending |

## Monitoring and retraining

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| P1 | Dashboard shows traffic, errors, latency and model readiness | pending | pending | pending | n/a | pending |
| P2 | `APIUnavailable` fires while production has no ready pod (screenshot 23 Aban, before the first deploy) and returns to Normal in Grafana after `deploy-prod`; the rule expression is the platform's | pending | pending | pending | n/a | pending |
| P3 | Drift report over two nonoverlapping windows | pending | n/a | pending | pending | pending |
| P4 | Delayed-label evaluation and the retraining recommendation with reasons and counts | pending | n/a | pending | pending | pending |
| P5 | Team Grafana-managed alerts (`monitoring/grafana/alert-rules.yaml`) evaluate on production data: Normal under normal traffic, firing when the condition is forced | pending | pending | pending | n/a | pending |

## Incident drill and handover

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| I1 | Incident drill: detection, action and result for each exercised case | pending | pending | pending | pending | pending |
| I2 | MLflow unavailable: the API keeps serving its loaded release | pending | pending | pending | n/a | pending |
| I3 | Runbook executed by a team member other than its author | pending | pending | pending | n/a | pending |
