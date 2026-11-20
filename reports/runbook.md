# Operations handover

Group g07. Helm release and Service name: `promiseops`.

| Namespace | What runs there | Who changes it |
|---|---|---|
| `g07-staging` | API (one replica), migration Job, suspended priority CronJob | CI: `deploy-staging`, `broken-readiness-staging` |
| `g07-prod` | API (2 to 3 replicas, HPA), migration Job, suspended priority CronJob | CI, started by `PROD_APPROVER` only |
| `g07-jobs` | queued training Jobs started by the company gateway | the gateway, after `POST /training` from Airflow |
| `g07-platform` | ConfigMaps `team-dags` and `team-code` read by the group's Airflow | CI: `airflow-sync` (update/patch only) |
| `g07-ci` | the CI jobs themselves (`Using Kubernetes namespace: g07-ci` in every job log) | the runner |

| Service | Address (login with the group's GitLab account) |
|---|---|
| GitLab | https://gitlab.mlops.qbootcamp.ir |
| Airflow | https://g07-airflow.mlops.qbootcamp.ir |
| MLflow | https://g07-mlflow.mlops.qbootcamp.ir |
| Grafana | https://g07-grafana.mlops.qbootcamp.ir |

Source of the addresses, the Grafana datasource and the scrape job: the course staff's answer
through the mentor on 23 Aban (2026-11-14); the datasource UID is the one used in
`monitoring/grafana/promiseops-dashboard.json` and `monitoring/grafana/alert-rules.yaml`.
Airflow details (no git-sync, ConfigMaps, worker settings) are from the staff's answer of
26 Aban and are repeated in `ci/airflow_sync.py`.

A command is **tested** when it has a row in `reports/evidence.md`; the row ID is given next
to it.

## 1. Startup from a clean environment

1. Run a pipeline on `main`. Test stage: `contracts`, `chart-lint` and, when
   `release/pin.json` exists, `fetch-release`: it downloads the pinned bundle from MLflow and
   checks hash, run and gate. If it fails, no image is built (section 4).
2. Build stage: `build-own-image` (the API image, with the pinned bundle) and `build-worker`
   (the training image). `build-worker` prints the two values the training DAG asks for:
   `worker_image: ...@sha256:...` and `source_commit: ...`.
3. Start `airflow-sync` (manual, `main` only). It writes `dags/*.py` to ConfigMap `team-dags`
   and each file of `src/promiseops` to ConfigMap `team-code` in `g07-platform`; Airflow
   picks them up a few minutes later. Then run the DAG `promiseops_env_check` in Airflow:
   all five tasks must be green (evidence O0). Airflow reads only what `airflow-sync` wrote
   from `main`; a DAG change takes effect after merge and a new `airflow-sync`.
4. Ingest the feed (section 3).
5. Start `deploy-staging`. Before the release is installed or upgraded, the chart runs
   `python -m promiseops.migrate` as a Job from the same image; it applies `sql/*.sql` in
   name order and the `deploy-staging` log ends with `{"applied": [...], "tables": [...]}`
   (12 tables today). Re-running is safe (K9). If the Job fails, the deploy fails and the
   previous release keeps serving.
6. Start `verify-staging` (A1). It uses `SMOKE_ORDER_ID` from `.gitlab-ci.yml`, today
   `feeaaeb6a99fc794212a29c84e6d527e` (released 2018-09-10). It answers 404 only while that
   order is not in the database, for example on an empty database before step 4.
7. Start `deploy-prod` (section 8).

Staging deploys stay manual: `STAGING_AUTO_DEPLOY` is `"false"` in `.gitlab-ci.yml`. It was
set so because until the runtime secret name was known and the image carried a model bundle
every pipeline on `main` would have failed in staging. Both are true now; switching it to
`"true"` is a change to `.gitlab-ci.yml` and needs a reviewed MR.

Migrations are never rolled back and must stay additive: a new table, a nullable column or
an index; never a drop or a rename.

## 2. Secret delivery

| Secret or setting | Where it lives | Used by |
|---|---|---|
| `runtime-config`: `PROMISEOPS_DATABASE_URL`, `PROMISEOPS_API_KEY`, `MLFLOW_TRACKING_URI` | Secret provided by the staff in `g07-staging` and `g07-prod` | API pods (`envFrom`); CI jobs read single keys from it |
| `registry-pull` | Secret provided by the staff in `g07-staging` and `g07-prod` | image pull |
| `COMPANY_GATEWAY_URL`, `COMPANY_FEED_KEY`, `PROMISEOPS_DATABASE_URL`, `MLFLOW_TRACKING_URI` | environment of the group's Airflow worker, set by the staff | ingestion and training DAGs (`os.environ`) |

- The feed key is never given to CI or to the team. There is no Airflow Connection or
  Variable to create; the DAGs read the environment (`dags/promiseops_env_check.py`,
  `src/promiseops/feed_sync.py`).
- The live check, `fetch-release`, `probe-database` and the drills read what they need from
  `runtime-config` of the namespace they work on; nothing is a CI/CD variable.
- Prometheus sends the `X-API-Key` header for `/metrics` itself, from the platform
  configuration.
- The chart creates no secret. No secret value is committed, printed in a job log or copied
  into evidence. `inspect-own-namespaces` shows secret names and key names only;
  `probe-database` compares the database URLs of staging and production by hash only.

## 3. Ingestion: retry and quarantine

Contract: `docs/design/data.md`. One batch is one transaction: all rows and the receipt, or
nothing. A rejected batch is rolled back and one row is written to `quarantine` with the
exact bytes and a `reason_code`.

Run it: Airflow → DAG `promiseops_ingest` → Trigger. Without configuration it ingests every
release the gateway lists; with `{"as_of": "2018-06-01T00:00:00Z"}` only releases available
by then. The task runs `python -m promiseops.feed_sync [--as-of ...]`, which reads the
gateway manifest, sorts by `available_at`, stores orders before outcomes, prints one JSON
line per file and stops at the first rejected file. Evidence D1, D2.

- Running the same batch again is safe: it returns the stored receipt with
  `replayed: true` and writes nothing (D2).
- A retry of a DAG run must keep its `as_of`.
- A rejected batch will not pass on retry. Read the reason, fix the cause at the source,
  then run it again. Never edit source rows by hand.
- The full feed takes long: 38 batches took 86 minutes on 27 Aban (an orders batch 4 to
  5.5 minutes, an outcomes batch 0.5 to 1.2 minutes); a replay of all 42 takes under a
  minute (section 11).

| `reason_code` | Meaning | Action |
|---|---|---|
| `checksum_mismatch` | file or payload differs from the feed manifest | download again |
| `json_invalid`, `schema_invalid` | file is not a valid batch | report (section 12) with the quarantine row |
| `future_publication` | batch published after the run's `as_of` | run with a later `as_of` |
| `duplicate_key`, `incomplete_order` | batch is inconsistent in itself | report (section 12) |
| `missing_fk` | a referenced row is not stored yet | ingest the earlier batches first |
| `batch_id_conflict` | this batch ID was accepted with another payload | report (section 12) |
| `committed_order_extended`, `immutable_row_changed` | batch tries to change stored facts | report (section 12) |

Counts without reading rows: CI job `probe-database` (row count per table, quarantine count
per reason). With database access, the last attempts:

```sql
SELECT quarantine_id, received_at, file_name, batch_id, reason_code, reason_detail
FROM quarantine ORDER BY quarantine_id DESC LIMIT 20;

SELECT batch_id, kind, available_at, feed_as_of, row_counts, ingested_at
FROM batch_receipts ORDER BY ingested_at DESC LIMIT 20;
```

## 4. Model lineage and release

Design: `docs/design/release.md`. The API reads one immutable bundle
(`release.json` and `model.joblib`) from `/opt/promiseops/release` at startup and never
contacts MLflow.

1. Train: Airflow → `promiseops_training` → Trigger with `worker_image` and `source_commit`
   from `build-worker` of the same `main` pipeline, `mode: train` and the cutoffs (defaults:
   the split in `docs/design/features-and-split.md`). The DAG sends `POST /training`; a 202
   means the request was queued, not that training finished. Follow the Job in `g07-jobs`
   (`inspect-own-namespaces`) and the run in MLflow (O2).
2. The Job records the run in the MLflow experiment `promiseops-g07` (parameters, metrics,
   gate decision, bundle under `bundle/`) and registers the model as `promiseops-late-risk`
   (O3). `model_version` is `<name>.v<MLflow version>.<sha8>`, immutable, never an alias.
3. Approve: a merge request that changes only `release/pin.json`, with the three values from
   `bundle/release.json` of that run (`release/README.md`). Only a run with gate `accepted`
   can be pinned. Example: !46.
4. After merge, `fetch-release` downloads and checks the bundle, `build-own-image` puts it in
   the image, and the image is deployed by digest (section 8).

**Precondition before `deploy-prod`:** `fetch-release` ran in the same pipeline and its log
shows `gate: accepted` and the pinned `model_version`. Without `release/pin.json` there is no
`fetch-release` and `build-own-image` builds the bootstrap bundle made by
`promiseops make-baseline`; nothing in the pipeline stops that image from reaching
production. The bootstrap bundle only proves the path; it is not a trained model.
Production ran it from its first deploy (27 Aban) until !46 (revision 4); it is also the
rollback target used in K5. After the deploy, Grafana → `promiseops_model_info` must show the
pinned `model_version`.

## 5. Readiness

- `/healthz`: 200 while the process lives.
- `/readyz`: 200 only with a loaded, validated model and a reachable database, else 503 with
  `{"status": "not_ready"}`.

When `/readyz` is 503:

1. Pods and events: CI job `inspect-own-namespaces` (pods, deployments, jobs, HPA, Helm
   releases of both namespaces).
2. Database: `runtime-config` present (`inspect-own-namespaces`) and the database reachable
   from the cluster (`probe-database`).
3. Model: if the database is reachable, the bundle is the cause. The API writes no log line
   when the bundle fails to load. `promiseops_model_ready` is 0 in that case, but a pod that
   is not ready may no longer be scraped, so No data is also possible. The bundle checks at
   startup are hash, feature contract and scikit-learn version; a bundle that passed
   `fetch-release` and the image build passes them.

A pod that is not ready receives no traffic; with `maxUnavailable: 0` the previous pods keep
serving and `helm --atomic` reverts the release (K4).

## 6. Metrics and alerts

Only production is scraped. The platform job `g07-production` reads
`promiseops-metrics.g07-prod.svc.cluster.local:8080/metrics` every 15 seconds. The headless
Service `promiseops-metrics` belongs to the platform and selects pods labelled
`app: promiseops`; the chart sets this label on the API pods and must not create that
Service.

Check that scraping works on the data itself, not on `kubectl describe` (on 27 Aban
`Endpoints: <none>` there was wrongly read as "not scraped" while the data was in
Prometheus). Grafana → Explore → `Team Prometheus`:

```promql
count by (model_version) (promiseops_model_info{job="g07-production"})
```

It must show one series with the deployed `model_version` and a count equal to the number
of ready replicas (K5).

Grafana datasource: `Team Prometheus`, UID `PBAB629F371D08AFE`. The dashboard
`monitoring/grafana/promiseops-dashboard.json` is imported by hand (P1).

| Alert | Owner | Condition | First check | Action |
|---|---|---|---|---|
| `APIUnavailable` | platform | production API not available | `promiseops_model_ready`, then section 5 | fix the cause or roll back (section 9) |
| `SlowPredictions` | platform | `/predict` latency (`promiseops_http_request_duration_seconds`) | request rate, HPA replicas, CPU | wait for scale-out or reduce the load (I1) |
| `RetrainingRecommended` | platform | `promiseops_retraining_required` = 1 | latest monitoring report | section 7 |
| `PromiseOpsModelNotReady` | team | `promiseops_model_ready == 0` for 2 min | section 5 | as `APIUnavailable` |
| `PromiseOpsHigh5xxRate` | team | 5xx share above 1% for 5 min | API logs (`Predict failed: exception_type=...`), last deploy | roll back if it started with a deploy |
| `PromiseOpsHighP95Latency` | team | p95 of all routes above 0.5 s for 5 min | as `SlowPredictions` | as `SlowPredictions` |

PrometheusRule objects are not supported. The team's rules are Grafana-managed rules on
`Team Prometheus`; their export is `monitoring/grafana/alert-rules.yaml`
(`monitoring/prometheus/alerts.yml` keeps the same expressions in Prometheus form). After a
change to the rules in Grafana, export them again and commit the file.
`PromiseOpsHigh5xxRate` uses `or vector(0)` so that it reads 0, not No data, when there is
no 5xx. This also hides a wrong label name, so after editing it check that the label exists:
`count by (status) (promiseops_http_requests_total{job="g07-production"})`.
The live rule in Grafana was changed first and the export on `main` follows it since !47
(2026-11-18); after any change in Grafana, compare the two before committing.

Notifications: the rules have been checked in Grafana (Alerting → Alert rules and their State
history, evidence P5); delivery of a notification has not been tested. Before relying on it,
check Alerting → Contact points (a contact point exists and its test message arrives) and
Alerting → Notification policies (the team's alerts are routed to it). Until then, watch
Alerting → Alert rules.

## 7. Delayed labels and retraining

Thresholds: `docs/CONTRACTS.md`, section "Monitoring and retraining". Evaluation uses only
outcomes available by `as_of`. Too few mature labels suppress automatic retraining.
Retraining never promotes: a new model still needs the gate, a pin MR and `deploy-prod`.

Run it: Airflow → `promiseops_training` with `mode: monitor-train`, the current
`worker_image` and `source_commit`, and `as_of` = the current time in UTC. Do not use a 2018
date here: the monitor counts predictions whose `created_at` is in the 60 days before
`as_of`, and predictions are created now. The Job writes one report; it trains only if the
report recommends retraining (P3, P4).

Each report is one row in `monitoring_reports`, unique per `(model_version, as_of)`, so a
retried Job writes nothing new. Too few mature labels store `retraining_required = false`
with the reason `insufficient_labels`. The API publishes the latest value as
`promiseops_retraining_required` (0 or 1), which the platform alert `RetrainingRecommended`
reads. `probe-database` shows the number of reports. The values are in the Job log and in
the table; the manual CI job `probe-monitoring` (read-only session) prints the latest five
rows with all columns (P3, P4). The query:

```sql
SELECT as_of, model_version, mature_orders, late_orders, recall_at_10,
       persistent_drift, retraining_required, reasons
FROM monitoring_reports ORDER BY as_of DESC, report_id DESC LIMIT 1;
```

## 8. Staging and human production approval

Design: `docs/design/platform.md`.

1. `deploy-staging` installs the image by digest with `helm --atomic` (K1).
2. `verify-staging` runs `acceptance/live_api.py` against the staging Service through its
   cluster address (port-forward is not allowed for CI). It may not fail: `deploy-prod` needs
   it, so a red check keeps the production button closed for that pipeline.
3. `deploy-prod` is manual on `main`. Its first step checks that the person who started it is
   `PROD_APPROVER` (`qbc13-g07-03`); anyone else fails before `helm` (K3). Changing the
   approver needs a reviewed MR. It refuses to start while a priority scoring Job is running
   in production, installs by digest with `helm --atomic`, runs the same live check and
   stores `deploy-prod.json` (K2).
4. Each deploy job stores a record (digest, commit, who started it, pipeline, time) as an
   artifact; the production records never expire.

Check section 4 (precondition) before starting `deploy-prod`.

## 9. Rollback by digest

1. Take the last healthy digest from `deploy-prod.json` of an earlier pipeline.
2. Build → Pipelines → Run pipeline on `main` with `ROLLBACK_DIGEST=sha256:<64 hex>`. In
   that pipeline nothing is built or deployed except `rollback-prod`.
3. Start `rollback-prod`. Like `deploy-prod` it checks `PROD_APPROVER` first, then the digest
   format. It deploys that digest with `migrations.enabled=false`, waits for readiness, runs
   the live check and stores `rollback-prod.json` (K5). Its `source_commit` is the commit of the
   rollback pipeline, not the one the image was built from; the digest is what identifies it.

The database schema is never rolled back: the older image runs on the newer schema, which is
why migrations must stay additive. A rollback does not retrain and does not touch source
data. The model is the one baked into the older image; check `promiseops_model_info`.
To go forward again, retry `deploy-prod` of the pipeline with the newer image (K5, job 2871).

## 10. Incident recovery

| Incident | How it shows | Recovery | Evidence row |
|---|---|---|---|
| Ingestion repeated or interrupted | same batch runs twice, or a run dies midway | run it again; counts must not change | D2, D5 |
| Broken schema | run fails, new row in `quarantine` | section 3 | D3 |
| Drift or late labels | monitoring report, `RetrainingRecommended` | section 7 | P3, P4 |
| Weaker candidate | gate rejects it | keep the champion; record the decision | M4 |
| Release with broken readiness | new pods never become ready | `helm --atomic` reverts; confirm the old pods serve | K4 |
| Pod deleted | one pod missing | Deployment replaces it; confirm readiness | K8 |
| Slow predictions under load | `SlowPredictions`, `PromiseOpsHighP95Latency` | wait for the HPA (at most 3 replicas within the 3 CPU budget); reduce the load | K7, I1 |
| MLflow unavailable | training, registration or `fetch-release` fails | API keeps serving; retry the Job or pipeline later | I2 |
| Bad version in production | errors or a wrong `model_version` after a deploy | section 9 | K5 |

For every exercised case record detection, action and result in `reports/evidence.md`.

Drill jobs (manual, `main` only; never two on one namespace at the same time; outputs in
`drill/` artifacts that never expire):

| Job | What it does | Evidence |
|---|---|---|
| `broken-readiness-staging` | upgrades staging with a missing model directory and polls `/readyz` | K4 |
| `kill-pod-prod` | deletes one production API pod while `/readyz` is polled; `PROD_APPROVER` only | K8 |
| `load-test-prod` | open-loop load on production while the HPA is watched | K6, K7 |

`load-test-prod` defaults to 40 requests/s for 240 s, which is above what production can
serve (K7). For an ordinary load test set `LOAD_RPS=10` and `LOAD_SECONDS=180` when starting
the job.

**Batch priority CronJob.** `promiseops-priority` exists in staging and production with
`suspend: true` (`values-staging.yaml`, `values-prod.yaml`). A manual run in staging is the
CI job `priority-run-staging` (`ci/priority_run.py`): it refuses any namespace that is not
`*-staging`, starts two Jobs from the CronJob's template with the same `PRIORITY_AS_OF`,
checks one `priority_runs` row, an identical second output and the contract shape, and
prints no raw Job log (A4, job 3402). To enable it in production: a reviewed MR
that sets `priorityCronJob.suspend: false` in `values-prod.yaml`, then `deploy-prod`. While
it runs, `deploy-prod` waits (section 8).

## 11. Limits

- Production budget: 3 CPU and 3 GiB for the namespace (`deploy/README.md`). Production runs
  2 to 3 API replicas at 750m CPU and 768 MiB each, which leaves room for one surge pod.
  Staging runs one replica without HPA.
- Training: one Job per team, 2 CPU and 4 GiB, `/tmp` capped at 2 GiB
  (`docs/CONTRACTS.md`).
- Ingestion inserts row by row: the full feed takes more than an hour (section 3). A batch
  insert (`executemany`) would shorten it; not done before the freeze.
- Capacity: 10 requests/s with p95 0.098 s (K6); 40 requests/s is beyond production (K7).
- GitLab has no protected environments here; approval is enforced by the job script.
- Team members have the Developer role: no CI/CD variables in project settings (values that
  must change, like `SMOKE_ORDER_ID`, live in `.gitlab-ci.yml` and change through an MR) and
  no `kubectl` access (everything on the cluster goes through CI jobs or Airflow).

## 12. Escalation

As in `CONTRIBUTING.md`: an infrastructure blocker (feed, database, MLflow, Airflow, the
cluster) is reported at once to Hassan, the team lead, with the time and the error text; he
passes it to the mentor, who passes it to the course staff. Include: namespace, pipeline or
job link, the command, the UTC time and the error text without secrets.
