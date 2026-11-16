# Operations handover

Group g07. Namespaces: `g07-staging`, `g07-prod`, `g07-jobs`, `g07-ci`. Helm release and
Service name: `promiseops`.

| Service | Address (login with the group's GitLab account) |
|---|---|
| GitLab | https://gitlab.mlops.qbootcamp.ir |
| Airflow | https://g07-airflow.mlops.qbootcamp.ir |
| MLflow | https://g07-mlflow.mlops.qbootcamp.ir |
| Grafana | https://g07-grafana.mlops.qbootcamp.ir |

Status of this document: skeleton. A command is marked **tested** only after it was run on
the real environment and has a row in `reports/evidence.md`; every other command is a draft.
Sections marked *open* wait for the part named there.

## 1. Startup from a clean environment

1. Run a pipeline on `main`: `contracts` and `chart-lint`, then `build-own-image` and
   `build-worker`.
2. Start `deploy-staging` (manual while `STAGING_AUTO_DEPLOY` is `"false"`). Before the
   release is installed or upgraded, the chart runs `python -m promiseops.migrate` as a Job
   from the same image; it applies `sql/*.sql` in name order and its output is printed in
   the `deploy-staging` log: `{"applied": [...], "tables": [...]}`. Re-running is safe. If
   the Job fails, the deploy fails and the previous release keeps serving
   (`docs/design/platform.md`).
3. Ingest the initial snapshot and the following batches (section 3).
4. Start `verify-staging`. Before any data is ingested it fails with 404 for the smoke
   order; that is expected.
5. Start `deploy-prod` (section 8).

Migrations are never rolled back and must stay additive: a new table, a nullable column or
an index; never a drop or a rename.

## 2. Secret delivery

| Secret | Where it lives | Used by |
|---|---|---|
| `runtime-config` (database URL, API key) | Secret provided by staff in staging and prod | API pods, through `envFrom` |
| `registry-pull` | Secret provided by staff in staging and prod | image pull |
| Feed key | Airflow connection | ingestion and training DAGs |

`runtime-config` also carries `MLFLOW_TRACKING_URI`. The live check in CI reads the API key
from `runtime-config` of the namespace it tests; it is not a CI variable. Prometheus sends
the `X-API-Key` header itself, from the platform configuration.

The chart creates no secret. No secret value is committed, printed in a job log or copied
into evidence. `inspect-own-namespaces` shows secret names and key names only.

*Open:* Airflow connection names.

## 3. Ingestion: retry and quarantine

Contract: `docs/design/data.md`. One batch is one transaction: all rows and the receipt, or
nothing. A rejected batch is rolled back and one row is written to `quarantine` with the
exact bytes and a `reason_code`.

- Running the same batch again is safe: it returns the stored receipt with
  `replayed: true` and writes nothing.
- A retry of a DAG run must keep its `as_of` and its batch files.
- DAG files reach Airflow by git-sync from `dags/` on `main`; a DAG change takes effect
  only after it is merged.
- A rejected batch will not pass on retry. Read the reason, fix the cause at the source,
  then run it again. Never edit source rows by hand.

| `reason_code` | Meaning | Action |
|---|---|---|
| `checksum_mismatch` | file or payload differs from the feed manifest | download again |
| `json_invalid`, `schema_invalid` | file is not a valid batch | report to staff with the quarantine row |
| `future_publication` | batch published after the run's `as_of` | run with a later `as_of` |
| `duplicate_key`, `incomplete_order` | batch is inconsistent in itself | report to staff |
| `missing_fk` | a referenced row is not stored yet | ingest the earlier batches first |
| `batch_id_conflict` | this batch ID was accepted with another payload | report to staff |
| `committed_order_extended`, `immutable_row_changed` | batch tries to change stored facts | report to staff |

Inspect the last attempts:

```sql
SELECT quarantine_id, received_at, file_name, batch_id, reason_code, reason_detail
FROM quarantine ORDER BY quarantine_id DESC LIMIT 20;

SELECT batch_id, kind, available_at, feed_as_of, row_counts, ingested_at
FROM batch_receipts ORDER BY ingested_at DESC LIMIT 20;
```

*Open:* the ingestion command and the DAG trigger steps (ingestion MR, issue #8).

## 4. Model lineage and release

Design: `docs/design/release.md`. The API reads one immutable bundle
(`release.json` and `model.joblib`) from `/opt/promiseops/release` at startup and never
contacts MLflow.

- `model_version` is immutable and never an alias.
- The bootstrap bundle built by `promiseops make-baseline` only proves the path. It is not
  a candidate and is never deployed to production.
- A trained model reaches production only through: gate result recorded, human approval in
  a merge request, image built with that bundle, deploy by digest.

A `train` Job records its run in the MLflow experiment `promiseops-g07` and registers the
model as `promiseops-late-risk`. The run holds the parameters, the metrics, the gate
decision and the bundle under `bundle/`; `release.json` names the run in its `mlflow`
section (`run_id`, `version`).

*Open:* the command that pins a trained bundle (issue #13).

## 5. Readiness

- `/healthz`: 200 while the process lives.
- `/readyz`: 200 only with a validated loaded model and a reachable database, else 503.

When `/readyz` is 503:

```bash
kubectl -n g07-staging get pods
kubectl -n g07-staging describe deployment promiseops
kubectl -n g07-staging logs deployment/promiseops --tail=100
```

Check in this order: the bundle checks at startup (hash, features, scikit-learn version),
then the database (secret present, URL reachable). A pod that is not ready receives no
traffic; the previous version keeps serving.

*Open:* the exact startup log lines to look for (issue #26).

## 6. Metrics and alerts

Only production is scraped. The platform job `g07-production` reads
`promiseops-metrics.g07-prod.svc.cluster.local:8080/metrics` every 15 seconds. The headless
Service `promiseops-metrics` belongs to the platform and selects pods labelled
`app: promiseops`; the chart must not create it. `/metrics` requires the API key.

Check in Grafana → Explore: `up{job="g07-production"}` is 1 for every replica.

Grafana datasource: `Team Prometheus` (default, provisioned). The dashboard
`monitoring/grafana/promiseops-dashboard.json` is imported by hand.

| Alert (provided by the platform) | First check | Action |
|---|---|---|
| `APIUnavailable` | `up{job="g07-production"}`, then section 5 | fix the cause or roll back (section 9) |
| `SlowPredictions` | HPA state, CPU throttling, database latency | wait for scale-out or reduce load |
| `RetrainingRecommended` | latest monitoring report | section 7 |

The platform rules read `promiseops_http_request_duration_seconds` (histogram,
`route="/predict"`) and `promiseops_retraining_required`. PrometheusRule objects are not
supported: the team's own extra alerts are Grafana-managed rules on the same datasource,
and their export is kept in the repository.

*Open:* the `promiseops_retraining_required` metric; export of the team's own alert rules.

## 7. Delayed labels and retraining

Thresholds: `docs/CONTRACTS.md`, section "Monitoring and retraining". Evaluation uses only
outcomes available by `as_of`. Too few mature labels suppress automatic retraining.
Retraining never promotes: the candidate still passes the gate and the human approval.

Each `monitor-train` report is one row in `monitoring_reports`, unique per
`(model_version, as_of)`, so a retried Job writes nothing new. Too few mature labels store
`retraining_required = false` with the reason `insufficient_labels`. Read the latest one:

```sql
SELECT as_of, model_version, mature_orders, late_orders, recall_at_10,
       persistent_drift, retraining_required, reasons
FROM monitoring_reports ORDER BY as_of DESC, report_id DESC LIMIT 1;
```

*Open:* the monitoring DAG (issue #14).

## 8. Staging and human production approval

Design: `docs/design/platform.md`.

1. `deploy-staging` installs the image by digest with `helm --atomic`.
2. `verify-staging` runs `acceptance/live_api.py` against the staging Service through its
   cluster address. Port-forward is not allowed for CI.
3. `deploy-prod` is manual on `main` and checks that the person who started it is
   `PROD_APPROVER`. Anyone else fails the job. Changing the approver needs a reviewed MR.
4. Each deploy job stores a record (digest, commit, who started it, pipeline, time) as an
   artifact.

Do not start `deploy-prod` before `verify-staging` is green for the same pipeline.

## 9. Rollback by digest

1. Take the last healthy digest from `deploy-prod.json` of that pipeline.
2. Build → Pipelines → Run pipeline on `main` with `ROLLBACK_DIGEST=sha256:...`.
3. Start `rollback-prod`: it deploys that digest, waits for readiness and runs the live
   check. It does not retrain and does not touch source data.

## 10. Incident recovery

| Incident | How it shows | Recovery | Evidence row |
|---|---|---|---|
| Ingestion repeated or interrupted | same batch runs twice, or a run dies midway | run it again; counts must not change | D2, D5 |
| Broken schema | run fails, new row in `quarantine` | section 3 | D3 |
| Drift or late labels | monitoring report | section 7 | P3, P4 |
| Weaker candidate | gate rejects it | keep the champion; record the decision | M4 |
| Release with broken readiness | new pods never become ready | Helm reverts; confirm the old digest serves | K4 |
| Pod deleted | one pod missing | Deployment replaces it; confirm readiness | K8 |
| MLflow unavailable | training or registration fails | API keeps serving; retry the Job later | I2 |
| Bad version in production | errors after a deploy | section 9 | K5 |

For every exercised case record detection, action and result in `reports/evidence.md`.

## 11. Limits

- Serving budget: 3 CPU and 3 GiB per namespace; production runs 2 to 3 replicas.
- Training: one Job per team, 2 CPU and 4 GiB, `/tmp` capped at 2 GiB.
- GitLab has no protected environments here; approval is enforced by the job script.
- Staging deploys are manual until the first stable deploy.

## 12. Escalation to staff

Shared infrastructure problems go to the mentor, who passes them to course staff. Include:
namespace, pipeline or job link, the command, the UTC time and the error text without
secrets.
