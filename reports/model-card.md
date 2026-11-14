# Model card

Complete: business scope; prediction time; target; cohort exclusions; leakage controls; temporal/label cutoffs; feature list; two model approaches; baseline; cohort size/prevalence; recall/precision at 10%; AP and Brier; lineage; limitations; candidate decision and reviewer.

## Bootstrap bundle (path test only)

- Command: `promiseops make-baseline --out release/`
- Model: `DummyClassifier(strategy="prior")` in a scikit-learn `Pipeline`, using the same 15
  features as the training and serving contract.
- Data: synthetic fixtures only (`fixtures/synthetic-orders.json` and
  `fixtures/synthetic-outcomes.json`), currently 300 orders with a 35% late rate.
- Purpose: let the API and staging path start before real data and a trained model exist.
- This is **not** a production model: no validation metrics, no candidate/champion gate, and
  it must never be promoted or deployed to production. The `model_version` contains
  `bootstrap` and the gate decision is recorded as `bootstrap` in `release.json`.
