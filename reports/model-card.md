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
| train | pending | pending | pending |
| validation | pending | pending | pending |
| test | pending | pending | pending |

These counts remain `pending` until the first real Postgres cohort is ingested and evaluated.
Cohorts with one class or too few mature labels are a reported failure, never fabricated
metrics.

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

The table is intentionally pending. It will be filled only after the first real training run;
synthetic fixture numbers are not model-quality evidence.

| Approach | N | Prevalence | K | Recall@10% | Precision@10% | AP | Brier |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | pending | pending | pending | pending | pending | pending | pending |
| logistic_regression | pending | pending | pending | pending | pending | pending | pending |
| hist_gradient_boosting | pending | pending | pending | pending | pending | pending | pending |

Validation cohort hash: `pending`.

## Monitoring

The operational monitor uses a seven-day feature window, a sixty-day prediction/label window,
at least 100 unique current orders, PSI for numeric drift and total variation distance for
categorical drift. Persistent drift requires two distinct nonoverlapping windows. With mature
labels it evaluates recall@10%, average precision and Brier against the recorded validation
reference. Insufficient labels suppress automatic retraining.

## Limitations and lineage

- The bootstrap bundle in the serving image is path-only, is not a production model and must
  never be promoted.
- The test cohort stays untouched until final evaluation; no result in this card is a
  production claim.
- A real run records commit, image digest, MLflow run ID, immutable model version,
  `model_sha256`, cohort/`as_of`, N, prevalence, K, cohort hash, metrics and gate decision in
  `reports/evidence.md` and MLflow.
- `model_version` format is `<name>.v<mlflow version>.<sha8>`; production never resolves a
  mutable alias.
