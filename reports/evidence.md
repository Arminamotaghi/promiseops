# Evidence index

Every row is one behavior shown on the real environment (the group's Airflow, MLflow, team
database, staging, production and Grafana). Each row says what was run, when, on which commit,
image and model, and where the raw result is. A row that was not run on the real environment
says so and why; nothing here is a result from a local machine presented as a cluster result.

## Where the raw results are

- **Job logs and artifacts in GitLab.** Deploy records (`deploy-staging.json`,
  `deploy-prod.json`, `rollback-prod.json`) and drill outputs (`drill/`) are job artifacts;
  the production and drill artifacts never expire. Job links are listed at the end.
- **Screenshots** (Airflow, MLflow, Grafana) are attached as comments on issue #18. The
  first line of each comment is the row ID, for example `P1`.
- No raw result in this repository or in an issue may contain a credential: API key,
  database URL, feed key or token. The CI jobs print key names and hashes only.

Times are UTC. Grafana screenshots show Tehran time (UTC+03:30); both are given where a
screenshot is the source. Dates are 2026-11-18 (27 Aban 1405) unless written otherwise.

## Releases referred to below

| Name | Commit | App image digest | Model version |
|---|---|---|---|
| Pinned v1 | `73ff5a78` (main after !46), pipeline #811 | `sha256:e77a6f5b6cb1f79f2fcfaf94e831fba143dfd0c1d03a7d5dee94611af96182ea` | `late-risk-logistic_regression.v1.aaf19136` |
| Bootstrap (rollback target in K5) | before !46 | `sha256:f9833761b92fc163235eeabc5c49b1fe84e8b71575e113ae3b3992d0fcdbfe04` | bootstrap bundle (path check only, not a trained model) |

`release/pin.json` (!46): MLflow run `ef405226c5c74b8994ba3cfed281da0e`, `model_sha256`
`aaf19136c646d3e0f44061d78e535bcd53dac991931f68ce0d7da9c19361ffbf`. In pipeline #811,
`fetch-release` downloaded that run's bundle, found the same hash and `gate: accepted`, and
only then were the images built.

Staging and production use the same database: `probe-database` printed
`staging and prod: SAME database` (pipeline #780, 21:19).

## Data and quality

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| D1 | Valid batch is committed with its receipt | team-code from `airflow-sync` (see O0) | n/a | n/a | all 42 gateway releases, `available_at` 2018-01-01 to 2018-11-05 | DAG `promiseops_ingest` (27 Aban). `probe-database` at 21:19: `batch_receipts` 42, `orders` 96,393, `outcomes` 96,393, `customers` 96,393, `items` 110,101, `payments` 100,668, `products` 32,199, `sellers` 2,970, `quarantine` 0. The first snapshot took 71 min and each two-week batch about 5 min, because `ingest_batch` inserts row by row (known limit, runbook section 11). Screenshot: #18 `O1` (pending) |
| D2 | Replaying the same batch changes no row count (`replayed: true`) | as D1 | n/a | n/a | same releases | Second full run of `promiseops_ingest`: no batch added, counts unchanged (the counts in D1 are after the replay). Screenshot: #18 `O1` (pending) |
| D3 | Broken batch is rejected whole and quarantined | n/a | n/a | n/a | n/a | **Not run on the shared database.** Staging and production share it, so a deliberately broken batch would leave a `quarantine` row in production data. Shown on local PostgreSQL 16 in the review of !23 (all four database-side rejections write a quarantine row and keep the connection open) and !30 (`missing_fk`). On the cluster: `quarantine` 0 after the full feed |
| D4 | Same batch ID with another payload is rejected | n/a | n/a | n/a | n/a | **Not run on the shared database** (same reason as D3). Local PostgreSQL in the review of !35: a changed payload gives `checksum_mismatch` and writes nothing |
| D5 | Interrupted ingestion leaves no partial rows and can be retried | n/a | n/a | n/a | n/a | **Not run on the cluster.** One batch is one transaction (`docs/design/data.md`); the retry path is D2 |

## Model and evaluation

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| M1 | Temporal split and label cutoffs (no future labels in training) | pending: worker commit from MLflow | pending: worker digest | `late-risk-logistic_regression.v1.aaf19136` | `train_end` 2017-12-31T23:59:59Z, `validation_end` 2018-03-31T23:59:59Z, `as_of` 2018-05-01T00:00:00Z (DAG defaults; pending: confirm in the MLflow run parameters) | Training Job `train-56e54b8eaea29d5b0d7189a3`, MLflow run `ef405226c5c74b8994ba3cfed281da0e` |
| M2 | Baseline and two approaches: N, prevalence, recall@10%, precision@10%, AP, Brier | as M1 | as M1 | as M1 | validation cohort, N = 20,181 | Selected: logistic regression, recall@10% 0.233, precision@10% 0.263. pending: baseline and gradient boosting rows, prevalence, AP and Brier from the MLflow run (also for `reports/model-card.md`) |
| M3 | Gate decision on identical validation IDs (cohort hash) | as M1 | as M1 | as M1 | as M2 | `gate: accepted` (first candidate against the training-prevalence baseline), confirmed again by `fetch-release` in pipeline #811. pending: `cohort_hash` from the MLflow run |
| M4 | Weaker candidate is rejected by the gate | pending | pending | pending | pending | pending: a second training run (v2) was recorded at 18:41; its gate decision is read from MLflow |

## Orchestration and lifecycle

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| O0 | Airflow worker pre-flight (`promiseops_env_check` after `airflow-sync`): four settings present, gateway, database and MLflow reachable, `promiseops` imports from `team-code` | pending: commit from the `airflow-sync` log | n/a | n/a | n/a | `airflow-sync` filled ConfigMaps `team-dags` and `team-code` in `g07-platform`; all five tasks of `promiseops_env_check` green. This run found that the gateway manifest lists `releases`, not `entries` (fixed in !44). Screenshot: #18 `O0` (pending) |
| O1 | Ingestion DAG run for a chosen `as_of`; retry of the same run is a no-op | as D1 | n/a | n/a | as D1 | See D1 and D2. Screenshot of both runs: #18 `O1` (pending) |
| O2 | Training DAG submits the queued Job (202 and Job name); the Job's final status | pending: as M1 | pending: as M1 | v1 | as M1 | `promiseops_training` (`mode: train`) → `POST /training` answered 202 → Job `train-56e54b8eaea29d5b0d7189a3` in `g07-jobs` → MLflow run `ef405226...` finished with `gate: accepted`. The DAG only submits; the final status is read from MLflow and `g07-jobs`, not from the DAG. Screenshot: #18 `O2` (pending) |
| O3 | Lineage in MLflow: artifact SHA256, commit, cutoffs, cohort hash, metrics, gate | as M1 | as M1 | v1 | as M1 | Run `ef405226c5c74b8994ba3cfed281da0e`, registered model `promiseops-late-risk` version 1, `model_sha256` as above. The pin (!46) names this run; `fetch-release` checked hash, run and gate before the build. Screenshot of the run page: #18 `O3` (pending) |
| O4 | Replay on a later period: predictions recorded first, evaluated after labels mature | pending | pending | pending | pending | pending: whether the v2 run of 18:41 was a later-period replay |

## API and tests

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| A1 | Live contract check: probes, 401 without key, prediction, metrics | `73ff5a78` | Pinned v1 | v1 | smoke order `feeaaeb6a99fc794212a29c84e6d527e` | `verify-staging` job 2846 (pipeline #811): `Live API contract passed` (`acceptance/live_api.py`: `/healthz` and `/readyz` 200, `/predict` 401 without key, a prediction with `0 <= risk_probability <= 1` and a model version, `/metrics` 401 without key and 200 with it). The same check passed inside every production deploy and rollback: jobs 2847, 2870, 2871 |
| A2 | Repeated request ID returns the original result; other order gives 409 | `73ff5a78` | Pinned v1 | v1 | as A1 | Same request sent twice in every live check above: identical answer. The 409 case is not in the live check; shown on local PostgreSQL in the review of !25 (also: forty concurrent requests with one request ID store one row) |
| A3 | Unknown order 404, invalid input 422 | `1b782348` (main after !27) | pipeline #606 image | bootstrap | smoke order before any data was ingested | 404 on staging: `verify-staging` in pipeline #606 (25 Aban) failed with `AssertionError: 404`, because the smoke order was not in the database yet. 422: local only, review of !25 |
| A4 | Batch priority (`score-priority`): cohort, scored count and top 10% stored as one `priority_runs` row; a rerun with the same `as_of` returns the same run and adds no row; output shape as `contracts/priority.example.json` | n/a | n/a | n/a | n/a | **Not run on the cluster.** The CronJob is suspended in staging and production (`values-*.yaml`), the team has no `kubectl` access and CI has no job that starts it; after the code freeze no such job is added. `priority_runs` is 0. Shown on local PostgreSQL in the review of !33: the first run scored the three orders approved in the 24 hours before `as_of`, a rerun returned the same run, an empty cohort was not stored. Runbook section 10 says how to enable it |

## Kubernetes and CI

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| K1 | Staging deploy by digest and `verify-staging` | `73ff5a78` | Pinned v1 | v1 | n/a | `deploy-staging` job 2845 (pipeline #811): `helm --atomic`, staging revision 19, `deploy-staging.json`; then `verify-staging` job 2846 passed (A1). First staging deploy: job 2529 (issue #28) |
| K2 | Production deploy with manual approval (approver recorded) | `73ff5a78` | Pinned v1 | v1 | n/a | `deploy-prod` job 2847 at 22:00, started by `qbc13-g07-03` (`PROD_APPROVER`) after `verify-staging` passed in the same pipeline: production revision 4, smoke check passed, `deploy-prod.json` |
| K3 | Production deploy refused for a user who is not the approver | `f845e560` | n/a | n/a | n/a | `deploy-prod` job 2855 (pipeline #780) started by `qbc13-g07-02`: stopped in the approver check with `Production changes must be started by qbc13-g07-03 (started by qbc13-g07-02)`, before `helm`; production was not touched |
| K4 | Release with broken readiness never receives traffic and is reverted | pending | staging image before #811 (bootstrap bundle) | bootstrap | n/a | `broken-readiness-staging` passed (27 Aban): the upgrade with a missing model directory was reverted by `helm --atomic` and `/readyz` kept answering through the Service. pending: job link, poll result and Helm history from `drill/` |
| K5 | Rollback to an earlier digest, readiness and one real prediction verified | `73ff5a78` | Bootstrap, then Pinned v1 | bootstrap, then v1 | n/a | Pipeline #819 with `ROLLBACK_DIGEST` = Bootstrap digest, `rollback-prod` job 2870 at 22:16: revision 5, `migrations.enabled=false`, smoke check (one real prediction) passed, `rollback-prod.json`. Back to v1 by retrying `deploy-prod` of #811, job 2871 at 22:28: revision 6, Pinned v1 digest, smoke passed. Grafana, `count by (model_version) (promiseops_model_info{job="g07-production"})`: v1 from about 22:01 (01:31 Tehran), bootstrap from about 22:17 (01:47), v1 again from about 22:29 (01:59), always 2 pods. Screenshot: #18 `K5` (pending upload) |
| K6 | Load test result (rate, duration, failures, latency) | pending | production image before !46 (bootstrap bundle) | bootstrap | random sample of 300 stored orders | `load-test-prod` at 10 requests/s: p95 0.098 s. pending: job link, duration, failures from `drill/load-report.json` |
| K7 | HPA scales 2 to 3 under load and back to 2 | pending | as K6 | bootstrap | as K6 | `load-test-prod` at 40 requests/s (about 19:40 to 19:48): p95 measured by the load tool 16.7 s, p95 inside the API 2.11 to 2.24 s (I1). pending: HPA replicas during and after the load from `drill/hpa-during-load.txt` and `drill/hpa-after-load.txt` |
| K8 | Deleted pod is replaced while the service keeps answering | pending | as K6 | bootstrap | n/a | `kill-pod-prod` passed: one API pod deleted while `/readyz` was polled through the Service every 0.5 s. pending: job link and poll result from `drill/` |
| K9 | Pre-deploy migration Job applies `sql/*.sql` on every deploy; files already applied change nothing | `73ff5a78` | Pinned v1 | n/a | n/a | `deploy-staging` job 2845: the migration Job applied `001`, `002`, `003` without error on the database that already held all tables and the full feed, and listed the same 12 tables. Rollback job 2870 ran with migrations disabled, so a schema is never rolled back |

## Monitoring and retraining

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| P1 | Dashboard shows traffic, errors, latency and model readiness | `73ff5a78` | Pinned v1 | v1 | n/a | `monitoring/grafana/promiseops-dashboard.json` on production data (job `g07-production`): Request Rate, Latency p95, Readiness and Model Version have data; HTTP 5xx shows No data because there was no 5xx response. Screenshot: #18 `P1` (pending upload) |
| P2a | `APIUnavailable` fires while production has no ready pod and returns to Normal after `deploy-prod` | n/a, then the first production deploy (27 Aban) | n/a, then the bootstrap bundle image | n/a, then bootstrap | n/a | Firing before the first production deploy (screenshot of 23 Aban, 2026-11-14). pending: screenshot of the same rule Normal after the first `deploy-prod`. Rule expression is the platform's |
| P2b | `SlowPredictions` fires under load | pending: as K7 | as K7 | bootstrap | as K7 | `ALERTS{alertname="SlowPredictions",alertstate="firing"}` = 1 from about 19:45 (23:15 Tehran) during the 40 requests/s test; p95 of `promiseops_http_request_duration_seconds` 2.11 to 2.24 s against the 0.5 s threshold. Screenshot: #18 `I1` (pending upload) |
| P3 | Drift report over two nonoverlapping windows | `f845e560` | worker `sha256:a73f1355...` (pending: full digest) | pending: from the Job log | `as_of` 2026-11-18T20:50:00Z | `promiseops_training` with `mode: monitor-train`: Job `train-2516b03e222b0209f598db15` Complete in 65 s; `probe-database` at 21:19: `monitoring_reports` 1. pending: drift values and reasons from the Job log |
| P4 | Delayed-label evaluation and the retraining recommendation with reasons and counts | as P3 | as P3 | as P3 | as P3 | `promiseops_retraining_required{job="g07-production"}` = 0 on both pods at 22:29 and 22:40 (01:59 and 02:10 Tehran), so `RetrainingRecommended` stays Normal. pending: `reasons` and counts from the Job log. Screenshot: #18 `P4` (pending upload) |
| P5 | Team Grafana-managed alerts (`monitoring/grafana/alert-rules.yaml`) evaluate on production data: Normal under normal traffic, firing when the condition is met | `db74ddaf` (rule file after !47) | Pinned v1 | v1 | n/a | All three rules Normal under normal traffic (screenshot pending upload). !47 made `PromiseOpsHigh5xxRate` return 0 instead of No data when there is no 5xx; the label `status` was checked to exist (values 200 and 401). pending: State history of the three rules for 19:40 to 20:00 (23:10 to 23:30 Tehran), when the p95 condition of `PromiseOpsHighP95Latency` was met |

## Incident drill and handover

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| I1 | Incident drill: detection, action and result for each exercised case | see each row | see each row | see each row | n/a | Slow predictions under load: detected by `SlowPredictions` (P2b); the load was bounded by the drill and stopped; pending: time the alert returned to Normal and the HPA reaction (K7). Bad version in production: K5. Broken readiness: K4. Lost pod: K8. Explained in the presentation: the gap between the load tool's p95 (16.7 s) and the API's p95 (about 2.2 s) is time requests waited before reaching the API |
| I2 | MLflow unavailable: the API keeps serving its loaded release | n/a | n/a | n/a | n/a | **Not run.** MLflow is shared and run by the course staff. By design the API reads the bundle baked into its image at startup and never calls MLflow (`docs/design/release.md`); only `fetch-release` and training Jobs need it |
| I3 | Runbook executed by a team member other than its author | pending | pending | pending | n/a | pending: the runbook task run by Ahmad |

## Job links

- 2529: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2529
- 2845: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2845
- 2846: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2846
- 2847: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2847
- 2855: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2855
- 2870: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2870
- 2871: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2871
- Pipelines: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/pipelines/811,
  https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/pipelines/819,
  https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/pipelines/780
