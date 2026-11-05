# Public contracts — version 1.0

## Dataset, replay and relational rows

The full prepared Olist dataset is available from the start. `GET /feed` on the internal company gateway requires the team's bearer feed key and returns all entries, with `as_of` equal to the last batch timestamp. `GET /feed/{file}` downloads any listed batch; unlisted paths return 404. For a team-controlled replay, `GET /feed?as_of=2018-01-15T00:00:00Z` returns only entries at or before that date and echoes the selected `as_of`. Use URL query encoding for timestamps containing `+`. This filter is optional, stateless and does not restrict downloading other batches. Gateway credentials identify the group for training; callers cannot select another team's identity.

Each entry has `payload_sha256` over UTF-8 JSON with sorted keys, `separators=(",", ":")`, `ensure_ascii=False`, no NaN; `file_sha256` hashes the actual file bytes. Do not confuse these checksums. Feed entries are processed in publication order, orders before outcomes at an equal timestamp.

Batch envelope: `schema_version="1.0"`, safe `batch_id`, timezone-aware `available_at`, `kind` (`orders` or `outcomes`), and row lists. Unknown columns are rejected. IDs are 1–128 characters matching `[A-Za-z0-9][A-Za-z0-9_.-]*`. Timestamps must contain an offset, are normalized to UTC, and are never interpreted using the server timezone. Currency values must be finite and nonnegative. The generated `contracts/*.schema.json` gives field types and bounds; cross-row and time rules below also apply.

| Table | Primary key | Required fields / relations |
|---|---|---|
| customers | customer_id | customer_state: two uppercase letters |
| sellers | seller_id | seller_state: two uppercase letters |
| products | product_id | category, weight_g |
| orders | order_id | customer_id FK, approved_at, promised_date |
| items | order_id + item_id | order_id/product_id/seller_id FKs, price, freight |
| payments | order_id + sequence | order_id FK, payment_type, installments, value |
| outcomes | order_id | order_id FK, delivered_at, available_at |

Orders batches contain complete orders with at least one item and payment. Source rows are immutable; duplicate identical dimension rows are reusable. Adding a new item/payment to an already committed order is rejected. An outcomes batch contains only outcomes. Delivery cannot precede approval, outcome availability cannot precede delivery, promise date cannot precede local approval date. Batch publication cannot precede the facts it contains.

A batch is validated and committed atomically with its receipt. Reject missing FKs, duplicate keys within a batch, changed immutable rows, negative/nonfinite amounts, future publication, incompatible schema and checksum failure. Preserve the rejected payload and reason in a private quarantine. Replaying an accepted batch ID with the same payload is a no-op; another payload under that ID is rejected. A failed attempt must not leave half the source rows or a success receipt.

Teams choose and record their own train/validation/test boundaries and replay `as_of`; there is no staff-controlled release schedule or clock approval. Loading the full dataset does not make future facts valid features at an earlier prediction time. Historical `outcomes.available_at` remains the label knowledge time, including the prepared data's artificial seven-day reporting delay. Batch `available_at` orders the optional replay. These timestamps and file checksums are unchanged. Wall clock controls infrastructure timeouts and approvals.

Choose chronological train, validation and test cohorts yourself; reserve the test cohort for final evaluation. For the lifecycle demonstration, choose a later period from the same dataset, record predictions first, then evaluate them as labels become available in your replay. Staff audits leakage and cutoff behavior using generated test variants.

## Prediction and labels

Predict at approval. `late = local_date(delivered_at) > promised_date`, using `America/Sao_Paulo`. The staff-selected cohort contains delivered orders with valid fields; cancellation risk is outside scope. Neither final state nor outcome fields enter features. `source order_id` is an identifier, not a predictor. Aggregate items and payments independently before combining. Historical aggregates, if added, must use only outcomes available before that order's prediction time.

Training orders are approved on/before `train_end` **and their labels are available on/before train_end**. Validation orders are approved after `train_end` and on/before `validation_end`; use only labels available on/before the evaluation `as_of`. Require `train_end < validation_end <= as_of`. Both classes and a sufficient cohort are required; an insufficient cohort is a reported failure, not fabricated metrics. Initial reference cutoffs: 2017-10-31 23:59:59 UTC / 2017-12-31 23:59:59 UTC / initial snapshot 2018-01-01 UTC. These are examples, not mandatory split dates. Choose and document your own chronological split and untouched test period; later periods can also support replay and rolling evaluation.

The default reference feature names appear in `contracts/features.json`; students may justify additional leakage-safe features. Training and serving use one fitted preprocessing pipeline. The API loads its release once at startup and does not require live MLflow to serve.

## API

`POST /predict`, header `X-API-Key`, body:

```json
{"order_id":"a-released-order-id","request_id":"optional-client-generated-id"}
```

Success: `order_id`, `request_id`, `risk_probability` in [0,1], nonempty immutable `model_version`. An absent request ID is generated. Repeated ID + same order returns the original result, including after deployment changes; using that ID for another order returns 409. Invalid input: 422. Unknown/not-yet-available order: 404. Missing/wrong key: 401. No model: 503. Do not return raw SQL exceptions or secrets.

`GET /healthz`: 200 while process lives. `GET /readyz`: 200 only with a validated loaded model and reachable DB; otherwise 503. Probes carry no secret. `GET /metrics` requires the API key and exports bounded labels: route template, status, a fixed set of feature names. No raw order IDs, request IDs, email addresses or credentials in metric labels. Structured prediction logs may include the request ID and model version.

Batch priority output contains schema version, company `as_of`, batch/cohort identity, model version, scored count, and the top `ceil(0.1*N)` orders. Sort descending risk, ties by ascending order ID. State how orders enter the cohort; daily production scoring should select newly released orders rather than repeatedly counting historical orders as new traffic.

## Queued worker interface

`POST /training` on the internal company gateway uses the team's bearer feed key. Request: `image` (own subgroup image by SHA256 digest), `source_commit` (40 lowercase hex characters), `train_end`, `validation_end`, `as_of` (aware timestamps), optional `mode` (`train` or `monitor-train`) and `request_id` (1–128 alphanumeric/underscore/dot/hyphen characters). Use a hash of Airflow's DagRun ID: retries reuse it; another scheduled run gets another ID. The team supplies `as_of`; the gateway enforces `train_end < validation_end <= as_of`, without a staff clock cap. Response 202 contains the Job name and own jobs namespace; it means submitted for admission, not completed training. Observe its status/logs in your own namespace.

The dispatcher invokes **`python -m promiseops.worker --mode MODE --train-end ... --validation-end ... --as-of ...`** inside your image. Implement that module using the provided stub. Environment: own-team `PROMISEOPS_DATABASE_URL`, `MLFLOW_TRACKING_URI`, `CI_COMMIT_SHA`, writable `PROMISEOPS_ARTIFACTS=/tmp/artifacts` and `PROMISEOPS_RELEASE=/tmp/artifacts/release.json`. Image root is read-only; `/tmp` is capped at 2 GiB. Each training container gets 2 CPU and 4 GiB, one per team and two globally. Bare Pods/Deployments and manual Job unsuspension are forbidden in the training namespace. Use staging/prod for ordinary serving and priority-scoring workloads.

Build the worker derivative from the exact serving image so it carries the incumbent bundle at `/opt/promiseops/release`. Copy that trusted bundle into writable artifacts before comparing a candidate. `train` fits/evaluates/registers; `monitor-train` first persists a report and fits only if retraining is recommended. Persist the exact bundle and metadata to your own MLflow, not only ephemeral Job files. Neither mode promotes/deploys production. Staff provides worker dependencies and registry access; your group implements model and monitoring behavior.

## Evaluation and release

Recall@10% = late orders in top K / all late orders; precision@10% = late orders in top K / K. K = max(1, ceil(0.1*N)); deterministic tie order is order ID. PR-AUC uses average precision, Brier is mean squared probability error. Report N and prevalence.

Candidate and champion are scored on **identical validation IDs**, recorded by cohort hash. Public reference gates: recall@10% cannot decrease; PR-AUC may decrease by at most 0.02; Brier may worsen by at most 0.005. Compare the first candidate with a constant training-prevalence baseline. Rejection is a valid lifecycle outcome. Do not lower gates just to force a promotion. A changed champion requires reevaluation.

A model record includes model artifact SHA256, immutable model identity, source commit, software versions, dataset/schema identity, train/evaluation cutoffs, label cutoff, cohort hash, metrics and gate decision. MLflow candidate registration does not promote production. Human approval prepares a trusted bundle; CI builds an image carrying that bundle and deploys its digest. Deployment evidence adds image digest and actual approver. Production startup never resolves a mutable alias. Rollback deploys a known earlier digest and verifies readiness; it does not retrain or roll back source data.

## Monitoring and retraining

Public reference: at least 100 unique current orders in a seven-day feature window. Numeric drift uses PSI, categorical drift uses total variation distance; 0.2 is a review threshold. Persistent drift requires two distinct, nonoverlapping feature windows. Retries/rescores of the same order do not inflate the sample count.

Delayed-label evaluation retains predictions for 60 company days. It uses only outcomes available by `as_of`, at least 100 unique mature orders, and both classes. Recommend retraining for persistent drift with sufficient mature outcomes, recall below the 10% random-review baseline, or degradation versus the recorded validation reference (recall <80%, AP down >0.05, or Brier up >0.02). Record exact reasons and counts. These are operational triggers, not guarantees that a replacement model will improve. Insufficient labels suppress automatic retraining; candidate quality gates and human production approval still apply.
