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
| Bootstrap (rollback target in K5) | `ba87743c` (main after !43), pipeline #746, `build-own-image` job 2522 | `sha256:f9833761b92fc163235eeabc5c49b1fe84e8b71575e113ae3b3992d0fcdbfe04` | `late-risk-bootstrap.v0.100626fb` (path check only, not a trained model) |

`release/pin.json` (!46): MLflow run `ef405226c5c74b8994ba3cfed281da0e`, `model_sha256`
`aaf19136c646d3e0f44061d78e535bcd53dac991931f68ce0d7da9c19361ffbf`. In pipeline #811,
`fetch-release` (job 2838, 21:35 to 21:37) downloaded that run's bundle, found the same hash
and `gate: accepted`, and only then were the images built.

Staging and production use the same database: `probe-database` printed
`staging and prod: SAME database` (pipeline #780, 21:19).

## Data and quality

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| D1 | Valid batch is committed with its receipt | team-code from `68fc86b8` (`airflow-sync` 10:39, pipeline #745) | n/a | n/a | run A `as_of` 2018-01-15T00:00:00Z; run B the whole manifest (`cutoff` 2018-11-05T00:00:00Z, 42 releases) | DAG `promiseops_ingest`. Run A (`manual__2026-11-18T10:47:56`, conf `{"as_of": "2018-01-15T00:00:00Z"}`) stored the first four batches. Run B (`manual__2026-11-18T11:11:26`, task 12:15:57 to 13:41:37): one JSON line per release, all `accepted`, 38 new and the 4 of run A `replayed: true`; summed `row_counts` match the database. `probe-database` (pipeline #746, 15:56): `batch_receipts` 42, `orders` 96,393, `outcomes` 96,393, `customers` 96,393, `items` 110,101, `payments` 100,668, `products` 32,199, `sellers` 2,970, `quarantine` 0. Run B took 86 min for 38 batches (an orders batch 4 to 5.5 min, an outcomes batch 0.5 to 1.2 min) because `ingest_batch` inserts row by row (runbook section 11). Screenshot: #18 `O1` (pending) |
| D2 | Replaying the same batch changes no row count (`replayed: true`) | as D1 | n/a | n/a | same releases | Run B replayed the four batches of run A (`replayed: true`). Two more replays by Mohammadreza: run C (`manual__2026-11-18T14:54:26`, `as_of` 2018-01-15, 4 releases in 16 s) and run D (`manual__2026-11-18T15:03:01`, all 42 releases in 38 s, against 86 min for the first ingestion). `probe-database` at 15:56, after both, shows the same counts as D1. Screenshot: #18 `O1` (pending) |
| D3 | Broken batch is rejected whole and quarantined | n/a | n/a | n/a | n/a | **Not run on the shared database.** Staging and production share it, so a deliberately broken batch would leave a `quarantine` row in production data. Shown on local PostgreSQL 16 in the review of !23 (all four database-side rejections write a quarantine row and keep the connection open) and !30 (`missing_fk`). On the cluster: `quarantine` 0 after the full feed |
| D4 | Same batch ID with another payload is rejected | n/a | n/a | n/a | n/a | **Not run on the shared database** (same reason as D3). Local PostgreSQL in the review of !35: a changed payload gives `checksum_mismatch` and writes nothing |
| D5 | Interrupted ingestion leaves no partial rows and can be retried | n/a | n/a | n/a | n/a | **Not run on the cluster.** One batch is one transaction (`docs/design/data.md`); the retry path is D2 |

## Model and evaluation

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| M1 | Temporal split and label cutoffs (no future labels in training) | `ba87743c` | worker `...407d37c7...` from `build-worker` of pipeline #746 (pending: full digest) | `late-risk-logistic_regression.v1.aaf19136` | `train_end` 2017-12-31T23:59:59Z, `validation_end` 2018-03-31T23:59:59Z, `as_of` 2018-05-01T00:00:00Z; `label_cutoff_train` = `train_end`, `label_cutoff_validation` = `as_of` | Training Job `train-56e54b8eaea29d5b0d7189a3` (bundle created 15:53), MLflow run `ef405226c5c74b8994ba3cfed281da0e`: the cutoffs are recorded in the run parameters and in `bundle/release.json` |
| M2 | Baseline and two approaches: N, prevalence, recall@10%, precision@10%, AP, Brier | as M1 | as M1 | as M1 | validation cohort: N 20,181, prevalence 0.1128, K 2,019 | From `bundle/release.json` of the run. Baseline: recall@10% 0.1015, precision@10% 0.1144, AP 0.1128, Brier 0.1055. Logistic regression: 0.2329, 0.2625, 0.2026, 0.1029. Gradient boosting: 0.2219, 0.2501, 0.2040, 0.1030. Selected: logistic regression (highest recall@10%). Table in `reports/model-card.md` |
| M3 | Gate decision on identical validation IDs (cohort hash) | as M1 | as M1 | as M1 | cohort hash `065d2ab0840cc32cafd447ca060fcc156b4c5006ce155a8811eee73bce2a66de` | First candidate against the training-prevalence baseline on the same cohort hash: `decision: accepted`, `reasons: []`. Confirmed again by `fetch-release` in pipeline #811 before the build |
| M4 | Weaker candidate is rejected by the gate | n/a | n/a | n/a | n/a | **Not shown on the cluster.** The second run (Job `train-a6cd364145811171b0e7e0a0`, 46 s; MLflow run `3c910ea0d07245a6947728d8d88fe470` at 18:41, version 2) used the same worker and cutoffs: same `model_sha256` and metrics, gate `accepted` (this shows reproducible training, not a rejection). Its worker image carried the bootstrap bundle, so it was again compared with the baseline. A rejection is covered by the tests (`tests/test_worker.py`: `recall_at_10 decreased` gives `rejected`) |

## Orchestration and lifecycle

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| O0 | Airflow worker pre-flight (`promiseops_env_check` after `airflow-sync`): four settings present, gateway, database and MLflow reachable, `promiseops` imports from `team-code` | `d2f5cb8f`, then `68fc86b8` | n/a | n/a | n/a | `airflow-sync` 09:10 (pipeline #727, `d2f5cb8f`): package as `promiseops.zip`. `promiseops_env_check` 09:17: `check_env`, `check_gateway`, `check_database`, `check_mlflow` succeeded, `check_team_code` failed (the ConfigMap is mounted as the directory `promiseops/`, so the zip could not be imported); the run also showed that the gateway manifest lists `releases`, not `entries`. Both fixed in !44. `airflow-sync` 10:39 (pipeline #745, `68fc86b8`): 18 module files, zip removed. `promiseops_env_check` 10:41: all five tasks succeeded. A later sync at 17:33 (`f845e560`) added the monitor-train code before the second training run and monitor-train. Screenshot: #18 `O0` (pending) |
| O1 | Ingestion DAG run for a chosen `as_of`; retry of the same run is a no-op | as D1 | n/a | n/a | as D1 | Run A with `as_of` 2018-01-15 stored only the releases available by then (4); runs B to D as in D1 and D2. Screenshot of the runs: #18 `O1` (pending) |
| O2 | Training DAG submits the queued Job (202 and Job name); the Job's final status | as M1 | as M1 | v1 | as M1 | `promiseops_training` run `manual__2026-11-18T15:52:20`, task `submit` 15:52:21 succeeded (the client accepts only 202) with `{'job': 'train-56e54b8eaea29d5b0d7189a3', 'namespace': 'g07-jobs', 'status': 'submitted-for-admission'}`. The DAG only submits; the final status comes from `g07-jobs` (`inspect-own-namespaces`, 19:08: the Job `Complete 1/1` in 95 s) and MLflow (run created 15:53:40, `FINISHED`, `gate_decision: accepted`). Screenshot: #18 `O2` (pending) |
| O3 | Lineage in MLflow: artifact SHA256, commit, cutoffs, cohort hash, metrics, gate | as M1 | as M1 | v1 | as M1 | Run `ef405226c5c74b8994ba3cfed281da0e`, registered model `promiseops-late-risk` version 1, `model_sha256` as above. The pin (!46) names this run; `fetch-release` checked hash, run and gate before the build. Screenshot of the run page: #18 `O3` (pending) |
| O4 | Replay on a later period: predictions recorded first, evaluated after labels mature | n/a | n/a | n/a | n/a | **Not run as a training replay.** A later training period would use the test window (April and May 2018), which stays untouched. Stored predictions were evaluated against labels available by `as_of` in the monitor-train run (P3, P4) |

## API and tests

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| A1 | Live contract check: probes, 401 without key, prediction, metrics | `73ff5a78` | Pinned v1 | v1 | smoke order `feeaaeb6a99fc794212a29c84e6d527e` | `verify-staging` job 2846 (pipeline #811, 21:58): `Live API contract passed` (`acceptance/live_api.py`: `/healthz` and `/readyz` 200, `/predict` 401 without key, a prediction with `0 <= risk_probability <= 1` and a model version, `/metrics` 401 without key and 200 with it). The same check passed inside every production deploy and rollback: jobs 2847, 2870, 2871 |
| A2 | Repeated request ID returns the original result; other order gives 409 | `73ff5a78` | Pinned v1 | v1 | as A1 | Same request sent twice in every live check above: identical answer. The 409 case is not in the live check; shown on local PostgreSQL in the review of !25 (also: forty concurrent requests with one request ID store one row) |
| A3 | Unknown order 404, invalid input 422 | `1b782348` (main after !27) | pipeline #606 image | bootstrap | smoke order before any data was ingested | 404 on staging: `verify-staging` in pipeline #606 (25 Aban) failed with `AssertionError: 404`, because the smoke order was not in the database yet. 422: local only, review of !25 |
| A4 | Batch priority (`score-priority`): cohort, scored count and top 10% stored as one `priority_runs` row; a rerun with the same `as_of` returns the same run and adds no row; output shape as `contracts/priority.example.json` | n/a | n/a | n/a | n/a | **Not run on the cluster.** The CronJob is suspended in staging and production (`values-*.yaml`), the team has no `kubectl` access and CI has no job that starts it; after the code freeze no such job is added. `priority_runs` is 0. Shown on local PostgreSQL in the review of !33: the first run scored the three orders approved in the 24 hours before `as_of`, a rerun returned the same run, an empty cohort was not stored. Runbook section 10 says how to enable it |

## Kubernetes and CI

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| K1 | Staging deploy by digest and `verify-staging` | `73ff5a78` | Pinned v1 | v1 | n/a | `deploy-staging` job 2845 (pipeline #811, 21:48 to 21:50, started by `qbc13-g07-03`): `helm --atomic`, staging revision 19, `deploy-staging.json`; then `verify-staging` job 2846 passed (A1). First green staging check: `deploy-staging` job 2528 (16:05, pipeline #746, Bootstrap digest, revision 14) and `verify-staging` job 2529 (16:09, `Live API contract passed`) (issue #28) |
| K2 | Production deploy with manual approval (approver recorded) | `73ff5a78` | Pinned v1 | v1 | n/a | `deploy-prod` job 2847 at 22:00 (helm finished 22:00:26), started by `qbc13-g07-03` (`PROD_APPROVER`) after `verify-staging` passed in the same pipeline: production revision 4, smoke check passed, `deploy-prod.json` with `started_by: qbc13-g07-03`, digest and commit. First production deploy: job 2530 (16:14 to 16:15, pipeline #746, Bootstrap digest): `Release "promiseops" does not exist. Installing it now.`, revision 1, smoke passed |
| K3 | Production deploy refused for a user who is not the approver | `f845e560` | n/a | n/a | n/a | `deploy-prod` of pipeline #780 started by `qbc13-g07-02`, done twice to be sure (the first attempt's screenshot was posted at 21:21); job 2855 is the reference: stopped in the approver check with `Production changes must be started by qbc13-g07-03 (started by qbc13-g07-02)`, before `helm`; production was not touched |
| K4 | Release with broken readiness never receives traffic and is reverted | `f845e560` | staging revision 16 (bootstrap bundle) | bootstrap | n/a | `broken-readiness-staging` job 2658, 19:59 to 20:02: staging revision 16 deployed; the upgrade with a missing model directory became revision 17 `failed` (`context deadline exceeded`) and `helm --atomic` rolled back (revision 18, `Rollback to 16`). `/readyz` polled through the Service during the whole 184 s: 327 answers, all 200, no failure |
| K5 | Rollback to an earlier digest, readiness and one real prediction verified | `73ff5a78` | Bootstrap, then Pinned v1 | bootstrap, then v1 | n/a | Pipeline #819 with `ROLLBACK_DIGEST` = Bootstrap digest, `rollback-prod` job 2870 at 22:16 (helm finished 22:16:31): revision 5, `migrations.enabled=false`, smoke check (one real prediction) passed, `rollback-prod.json` (its `source_commit` is the rollback pipeline's commit, not the commit the image was built from). Back to v1 by retrying `deploy-prod` of #811, job 2871 at 22:28 (helm finished 22:28:50): revision 6, Pinned v1 digest, smoke passed; the pod IPs changed (`.232`/`.238` before, `.117`/`.118` after), so new pods were started. Grafana, `count by (model_version) (promiseops_model_info{job="g07-production"})`: v1 from about 22:01 (01:31 Tehran), bootstrap from about 22:17 (01:47), v1 again from about 22:29 (01:59), always 2 pods. Screenshot: #18 `K5` (pending upload) |
| K6 | Load test result (rate, duration, failures, latency) | `f845e560` | production revision 2 (bootstrap bundle) | bootstrap | random sample of 300 stored orders | `load-test-prod` job 2828 at 20:09 with `LOAD_RPS=10`, `LOAD_SECONDS=180`: 1,800 requests, all 200, no transport error, p95 0.098 s (request 0.096 s, dispatch delay 0.002 s), `passed: true`. CPU per pod about 53% of request, below the 70% target, so the HPA stayed at 2 |
| K7 | HPA scales 2 to 3 under load and back to 2 | `f845e560` | as K6 | bootstrap | as K6 | `load-test-prod` job 2656 with the defaults 40 requests/s for 240 s (19:41:25 to 19:45:43): CPU 272% of request at 19:42:11 and 3 ready pods; HPA events `New size: 3; reason: cpu resource utilization (percentage of request) above target` about 19:42 and `New size: 2; reason: All metrics below target` about 19:48; 2 ready pods again at 19:48:06. The load itself did not pass (job failed, `passed: false`): 9,600 requests, all 200, no transport error, but p95 16.7 s (I1) |
| K8 | Deleted pod is replaced while the service keeps answering | `f845e560` | as K6 | bootstrap | n/a | Run twice. `kill-pod-prod` job 2657: pod `promiseops-7d6db66454-2k7ls` deleted at 19:56:33, replacement `...-vh8n4`, all pods ready at 19:57:11. Job 2816: pod `...-pst98` deleted at 20:03:00, replacement `...-nqfvq`, ready at 20:03:38. Each time `/readyz` through the Service for 47.9 s: 85 answers, all 200, no failure |
| K9 | Pre-deploy migration Job applies `sql/*.sql` on every deploy; files already applied change nothing | `73ff5a78` | Pinned v1 | n/a | n/a | `deploy-staging` job 2845: the migration Job applied `001`, `002`, `003` without error on the database that already held all tables and the full feed, and listed the same 12 tables. Rollback job 2870 ran with migrations disabled, so a schema is never rolled back |

## Monitoring and retraining

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| P1 | Dashboard shows traffic, errors, latency and model readiness | `73ff5a78` | Pinned v1 | v1 | n/a | `monitoring/grafana/promiseops-dashboard.json` on production data (job `g07-production`), 22:00 to 22:14 (01:30 to 01:44 Tehran): Request Rate and Latency p95 per route, Readiness READY for both pods, Model Version showing the switch from bootstrap to v1; HTTP 5xx shows No data because there was no 5xx response. Screenshot: #18 `P1` (pending upload) |
| P2a | `APIUnavailable` fires while production has no ready pod and returns to Normal after `deploy-prod` | n/a, then `ba87743c` | n/a, then Bootstrap | n/a, then bootstrap | n/a | Firing before the first production deploy (screenshot of 23 Aban, 2026-11-14). First production deploy: job 2530, 16:15. pending: screenshot of the same rule Normal after the first `deploy-prod`. Rule expression is the platform's |
| P2b | `SlowPredictions` fires under load | `f845e560` | as K7 | bootstrap | as K7 | `ALERTS{alertname="SlowPredictions",alertstate="firing"}` = 1 from about 19:45 (23:15 Tehran) during the 40 requests/s test; p95 of `promiseops_http_request_duration_seconds` for `route="/predict"` from 2.24 s (19:41:45) down to 2.11 s (19:48) against the 0.5 s threshold. Screenshot: #18 `I1` (pending upload) |
| P3 | Drift report over two nonoverlapping windows | `f845e560` | worker `sha256:a73f1355e423c6d1d4837ff7a60c924e0a1472d3cc115db15cc16ef7b9868565` | pending: from the Job log | `as_of` 2026-11-18T20:50:00Z | `promiseops_training` with `mode: monitor-train`: Job `train-2516b03e222b0209f598db15` (created about 21:04) Complete in 65 s; `probe-database` at 21:19: `monitoring_reports` 1. pending: drift values and reasons from the Job log |
| P4 | Delayed-label evaluation and the retraining recommendation with reasons and counts | as P3 | as P3 | as P3 | as P3 | `promiseops_retraining_required{job="g07-production"}` = 0 on both pods at 22:29 and 22:40 (01:59 and 02:10 Tehran), so `RetrainingRecommended` stays Normal. pending: `reasons` and counts from the Job log. Screenshot: #18 `P4` (pending upload) |
| P5 | Team Grafana-managed alerts (`monitoring/grafana/alert-rules.yaml`) evaluate on production data: Normal under normal traffic, firing when the condition is met | `db74ddaf` (rule file after !47) | Pinned v1 | v1 | n/a | All three rules (`PromiseOpsModelNotReady`, `PromiseOpsHigh5xxRate`, `PromiseOpsHighP95Latency`) Normal under normal traffic (screenshot of 21:24, pending upload). !47 made `PromiseOpsHigh5xxRate` return 0 instead of No data when there is no 5xx; the label `status` was checked to exist (values 200 and 401). pending: State history of the three rules for 19:40 to 20:00 (23:10 to 23:30 Tehran), when the p95 condition of `PromiseOpsHighP95Latency` was met |

## Incident drill and handover

| ID | Behavior | Commit | Image digest | Model version | Company clock/cohort | Command and result |
|---|---|---|---|---|---|---|
| I1 | Incident drill: detection, action and result for each exercised case | see each row | see each row | see each row | n/a | Slow predictions under load: detected by `SlowPredictions` (P2b); the load was bounded by the drill and stopped; the HPA went to 3 replicas about 19:42 and back to 2 about 19:48 (K7); pending: time the alert returned to Normal. Bad version in production: K5. Broken readiness: K4. Lost pod: K8. Explained in the presentation: the load tool sent on time (dispatch delay p95 0.006 s) but waited 16.7 s for answers, while the API measured about 2.2 s inside the handler; the difference is time the requests spent queued in front of the handler, which the API's own histogram does not see |
| I2 | MLflow unavailable: the API keeps serving its loaded release | n/a | n/a | n/a | n/a | **Not run.** MLflow is shared and run by the course staff. By design the API reads the bundle baked into its image at startup and never calls MLflow (`docs/design/release.md`); only `fetch-release` and training Jobs need it |
| I3 | Runbook executed by a team member other than its author | pending | pending | pending | n/a | pending: the runbook task run by Ahmad |

## Job links

- 2522: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2522
- 2528: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2528
- 2529: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2529
- 2530: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2530
- 2656: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2656
- 2657: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2657
- 2658: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2658
- 2816: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2816
- 2828: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2828
- 2845: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2845
- 2846: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2846
- 2847: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2847
- 2855: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2855
- 2870: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2870
- 2871: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/jobs/2871
- Pipelines: https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/pipelines/811,
  https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/pipelines/819,
  https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/pipelines/780,
  https://gitlab.mlops.qbootcamp.ir/qbc13/g07/promiseops/-/pipelines/746
