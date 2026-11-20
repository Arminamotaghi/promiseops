# Model card — late-delivery risk

## Purpose

PromiseOps estimates the probability that an order will be delivered late at the moment the
order is approved. Operations can review only about 10% of orders, so the primary metric is
recall@10%; precision@10%, PR-AUC (average precision) and Brier are also reported.

## Prediction time and target

- Prediction time: `orders.approved_at`; only facts known at approval are used.
- Target: `late = local_date(delivered_at) > promised_date` in `America/Sao_Paulo`.
  Delivery on the promised local day is on time.
- Cancellation risk is out of scope.

## Leakage controls

- `outcomes`, final status, reviews, dispatch/delivery timestamps, `released_at`, `batch_id`,
  `order_id` and `customer_id` are never model inputs.
- Training labels require both `approved_at <= train_end` and
  `outcomes.available_at <= train_end`.
- `items` and `payments` are aggregated separately before joining to orders.
- Historical features are not used in v1.
- `released_at` is not used for backtests; it only selects newly released orders for batch
  priority.

## Temporal split

The split is the binding team contract in `docs/design/features-and-split.md`.

| Cohort | `approved_at` range | Label cutoff | Use |
|---|---|---|---|
| train | `<= 2017-12-31T23:59:59Z` | `outcomes.available_at <= 2017-12-31T23:59:59Z` | fit and model selection |
| validation | `(2017-12-31T23:59:59Z, 2018-03-31T23:59:59Z]` | `available_at <= 2018-05-01T00:00:00Z` | candidate/champion gate |
| test | `(2018-03-31T23:59:59Z, 2018-05-31T23:59:59Z]` | `available_at <= 2018-06-07T23:59:59Z` | final evaluation only; untouched |
| replay | from `2018-06-01T00:00:00Z` | labels available at each run's `as_of` | operational replay |

- `validation_as_of = 2018-05-01T00:00:00Z`; it is late enough for all validation labels.
- `test_label_as_of = 2018-06-07T23:59:59Z`; the test cohort is not used for model selection.
- Replay starts after the test window so the windows do not overlap.

## Label maturity and excluded orders

A label is known only at `outcomes.available_at`, not at delivery time. Because the feed has a
seven-day reporting delay, orders near a cutoff are excluded from that cohort when their label
has not arrived. The exclusion count is part of the evaluation record; it is not silently
dropped.

| Cohort | Orders approved in window | Orders with mature labels | Excluded: label unavailable |
|---|---:|---:|---:|
| train | not recorded | not recorded | not recorded |
| validation | not recorded | 20,181 | not recorded |
| test | not evaluated | not evaluated | not evaluated |

The worker records N for the validation cohort only (orders with a label available by
`label_cutoff_validation`); the other counts are not written to the release metadata or
MLflow by v1 of the worker. Cohorts with one class or too few mature labels are a reported
failure, never fabricated metrics.

## Features

The 15 features and their order are fixed by `contracts/features.json` and implemented by
`src/promiseops/features.py`. Feature contract hash:
`2dbf79e7bc30c5d5744e113575120c14972fb17905a7784dfe44441185763fc5`.

1. `item_count`
2. `seller_count`
3. `total_price`
4. `total_freight`
5. `total_weight_g`
6. `payment_total`
7. `max_installments`
8. `promise_days`
9. `approval_hour`
10. `approval_weekday`
11. `interstate_fraction`
12. `customer_state`
13. `primary_seller_state`
14. `primary_category`
15. `primary_payment_type`

## Approaches

All approaches use the same 15-feature frame, the same fitted preprocessing pipeline and the
same validation order IDs.

- Baseline: `DummyClassifier(strategy="prior")`, a constant training-prevalence probability.
- Logistic regression: `StandardScaler` + `OneHotEncoder(handle_unknown="ignore")` +
  `LogisticRegression`.
- Histogram gradient boosting: the same preprocessing +
  `HistGradientBoostingClassifier`.

Selection first maximises recall@10%, then average precision, then lower Brier. The selected
candidate must still pass the champion gate below.

## Metrics and gate

- `K = max(1, ceil(0.1 * N))`; sort probability descending, ties by `order_id` ascending.
- `recall@10% = late orders in top K / all late orders`.
- `precision@10% = late orders in top K / K`.
- PR-AUC uses average precision; Brier is the mean squared probability error.
- Candidate and champion must be scored on identical validation IDs and the same
  `cohort_hash`; the hash is sha256 of the sorted unique order IDs joined by newline.
- Public gates: recall@10% must not decrease; PR-AUC may drop by at most 0.02; Brier may worsen
  by at most 0.005.
- The first candidate is compared with the constant training-prevalence baseline; later
  candidates are compared with the incumbent champion. A rejected weaker candidate is a valid
  outcome; gates are never lowered to force a candidate through.

## Evaluation status

First real training run on the team database (27 Aban 1405, 2026-11-18 15:53 UTC): queued
Job from the DAG `promiseops_training`, worker built from commit
`ba87743cb4891508d47d26320cbd6e26377a2cb7`, cutoffs as in the split above
(`train_end` 2017-12-31T23:59:59Z, `validation_end` 2018-03-31T23:59:59Z,
`as_of` 2018-05-01T00:00:00Z). MLflow experiment `promiseops-g07`, run
`ef405226c5c74b8994ba3cfed281da0e`, registered model `promiseops-late-risk` version 1.

Validation cohort (same order IDs for every approach):

| Approach | N | Prevalence | K | Recall@10% | Precision@10% | AP | Brier |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 20,181 | 0.1128 | 2,019 | 0.1015 | 0.1144 | 0.1128 | 0.1055 |
| logistic_regression | 20,181 | 0.1128 | 2,019 | **0.2329** | **0.2625** | 0.2026 | **0.1029** |
| hist_gradient_boosting | 20,181 | 0.1128 | 2,019 | 0.2219 | 0.2501 | **0.2040** | 0.1030 |

Validation cohort hash:
`065d2ab0840cc32cafd447ca060fcc156b4c5006ce155a8811eee73bce2a66de`.

- Selected: `logistic_regression`, the highest recall@10%. Gradient boosting has a slightly
  higher AP (0.2040 against 0.2026) but selection compares recall@10% first.
- Reviewing the top 10% finds 23.3% of all late orders, against 10.1% for the constant
  baseline (about 2.3 times as many); about one in four reviewed orders is late
  (precision 0.2625 against a prevalence of 0.1128).
- Gate: first candidate, compared with the training-prevalence baseline on the same cohort
  hash: `accepted`, no gate failed (recall not lower, AP not lower by more than 0.02,
  Brier not worse by more than 0.005).
- Released: `model_version` `late-risk-logistic_regression.v1.aaf19136`, `model_sha256`
  `aaf19136c646d3e0f44061d78e535bcd53dac991931f68ce0d7da9c19361ffbf`, pinned in
  `release/pin.json` (!46) and deployed to production by digest on 2026-11-18 22:00 UTC.
- Reproducibility: a second run with the same worker and cutoffs (18:41 UTC, run
  `3c910ea0d07245a6947728d8d88fe470`, version 2) produced a model with the same
  `model_sha256` and the same metrics.
- The test cohort has not been evaluated.

## Monitoring

The operational monitor uses a seven-day feature window, a sixty-day prediction/label window,
at least 100 unique current orders, PSI for numeric drift and total variation distance for
categorical drift. Persistent drift requires two distinct nonoverlapping windows. With mature
labels it evaluates recall@10%, average precision and Brier against the recorded validation
reference. Insufficient labels suppress automatic retraining.

## Limitations and lineage

- The bootstrap bundle in the serving image is path-only, is not a production model and must
  never be promoted.
- The test cohort stays untouched until final evaluation; the numbers above are validation
  results, not a production claim.
- A real run records commit, image digest, MLflow run ID, immutable model version,
  `model_sha256`, cohort/`as_of`, N, prevalence, K, cohort hash, metrics and gate decision in
  `reports/evidence.md` and MLflow.
- `model_version` format is `<name>.v<mlflow version>.<sha8>`; production never resolves a
  mutable alias.
