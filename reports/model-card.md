# Model card

## Purpose

PromiseOps estimates the probability that an order will be delivered late, at the moment
the order is approved. Operations can review only about 10% of orders, so the primary
metric is recall@10%; precision@10%, PR-AUC (average precision) and Brier are also
reported.

## Prediction time and target

- Prediction time: `orders.approved_at` (only facts known at approval are used).
- Target: `late = local_date(delivered_at) > promised_date` in `America/Sao_Paulo`.
  Same local day is on time.
- Cancellation risk is out of scope.

## Leakage controls

- `outcomes`, final status, reviews, dispatch/delivery timestamps, `released_at`,
  `batch_id`, `order_id` and `customer_id` are never model inputs.
- Training labels require `outcomes.available_at <= train_end`; old order age alone is not
  enough.
- `items` and `payments` are aggregated separately before joining to orders.
- Historical features are not used in v1.
- `released_at` is not used for backtests; it only selects newly released orders for batch
  priority.

## Features

The 15 features and their order are defined in `contracts/features.json` and implemented in
`src/promiseops/features.py`. Feature contract hash:
`2dbf79e7bc30c5d5744e113575120c14972fb17905a7784dfe44441185763fc5`.

## Temporal split

- Train: `approved_at <= 2017-12-31T23:59:59Z` and
  `outcomes.available_at <= 2017-12-31T23:59:59Z`.
- Validation: `(2017-12-31T23:59:59Z, 2018-03-31T23:59:59Z]` with
  `outcomes.available_at <= 2018-05-01T00:00:00Z`.
- Test: `(2018-03-31T23:59:59Z, 2018-05-31T23:59:59Z]`; kept untouched for final evaluation.
- Replay: from `2018-06-01T00:00:00Z`.

## Scope of this MR

This MR implements the training and candidate/champion comparison path only. The test and
replay windows above are binding team contract (`docs/design/features-and-split.md`), but
their scoring and evaluation code is **not** part of this MR: the public model API exposes
`load_training_frames`, `train_candidate`, `evaluate_predictions` and `compare_candidate`.
The test cohort stays untouched until final evaluation, and replay evaluation is deferred to
the monitoring work (Issue #13). No test or replay API is exposed yet.

## Approaches

All approaches use the same feature frame and validation order IDs. Candidate and champion
must share the same `cohort_hash`.

- Baseline: `DummyClassifier(strategy="prior")` (constant prevalence).
- Logistic regression: `StandardScaler` + `OneHotEncoder(handle_unknown="ignore")` +
  `LogisticRegression`.
- Histogram gradient boosting: the same preprocessing +
  `HistGradientBoostingClassifier`.

## Metrics

- `K = max(1, ceil(0.1 * N))`; sort probability descending, ties by `order_id` ascending.
- `recall@10% = late orders in top K / all late orders`.
- `precision@10% = late orders in top K / K`.
- PR-AUC uses average precision; Brier is the mean squared probability error.
- Public gates: recall@10% must not decrease; PR-AUC may drop by at most 0.02; Brier may
  worsen by at most 0.005.

## Results

The table below is a **synthetic smoke run** on the small SQLite fixture used by
`tests/test_model.py` (8 train orders, 4 validation orders, 2 late in validation,
`K = 1`). It proves the pipeline and metrics work; it is **not** a production evaluation.
Replace this table after the first real Postgres cohort is ingested.

| Approach | N | Prevalence | K | Recall@10% | Precision@10% | AP | Brier |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 4 | 0.50 | 1 | 0.50 | 1.00 | 0.50 | 0.2500 |
| logistic_regression | 4 | 0.50 | 1 | 0.00 | 0.00 | 0.4167 | 0.8305 |
| hist_gradient_boosting | 4 | 0.50 | 1 | 0.50 | 1.00 | 0.50 | 0.2500 |

Validation cohort hash (smoke): `944e5c830778...`

## Bootstrap bundle (path test only)

- Command: `promiseops make-baseline --out release/`
- Model: `DummyClassifier(strategy="prior")` in a scikit-learn `Pipeline`, using the same
  15 features as the training and serving contract.
- Data: synthetic fixtures only (`fixtures/synthetic-orders.json` and
  `fixtures/synthetic-outcomes.json`), currently 300 orders with a 35% late rate.
- Purpose: let the API and staging path start before real data and a trained model exist.
- This is **not** a production model: no validation metrics, no candidate/champion gate, and
  it must never be promoted or deployed to production. The `model_version` contains
  `bootstrap` and the gate decision is recorded as `bootstrap` in `release.json`.

## Limitations and lineage

- The current results are synthetic and too small to support model-quality claims.
- A real run must record commit, image digest, MLflow run ID, model version, cohort/as_of,
  N, prevalence, K, cohort hash, metrics and gate decision in `reports/evidence.md`.
- A rejected weaker candidate is a valid outcome; gates must not be lowered to force a
  candidate through.
- Reviewer: model owner (Ahmad) and the data reviewer (Mohammadreza) for feature/split
  consistency.
