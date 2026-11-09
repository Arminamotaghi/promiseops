-- PromiseOps / g07 — migration 002: prediction store (idempotency) + batch priority runs.
-- DRAFT CONTRACT (owner: Armina). Depends on 001 (orders).

BEGIN;

-- One row per request_id. This IS the idempotency store: a repeated request_id for the same
-- order returns this row unchanged, even after a new model is deployed.
CREATE TABLE IF NOT EXISTS predictions (
    request_id        text PRIMARY KEY CHECK (request_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'),
    order_id          text NOT NULL REFERENCES orders (order_id),
    risk_probability  double precision NOT NULL CHECK (risk_probability >= 0 AND risk_probability <= 1),
    model_version     text NOT NULL CHECK (length(model_version) > 0),
    source            text NOT NULL CHECK (source IN ('api', 'batch')),
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS predictions_order_id_idx ON predictions (order_id);
CREATE INDEX IF NOT EXISTS predictions_created_at_idx ON predictions (created_at);

-- One row per batch-priority run (CronJob). Re-running the same cohort with the same model
-- returns the stored list instead of creating a duplicate.
CREATE TABLE IF NOT EXISTS priority_runs (
    run_id         text PRIMARY KEY,           -- sha256(cohort_hash || model_version)
    as_of          timestamptz NOT NULL,       -- company as_of used to select the cohort
    cohort_id      text NOT NULL,              -- e.g. released:(2018-06-01,2018-06-15]
    cohort_hash    char(64) NOT NULL,
    model_version  text NOT NULL,
    scored_count   integer NOT NULL CHECK (scored_count >= 0),
    k              integer NOT NULL CHECK (k >= 1),
    output         jsonb NOT NULL,             -- exactly the published JSON (contracts/priority.example.json)
    created_at     timestamptz NOT NULL DEFAULT now()
);

COMMIT;
