-- PromiseOps / g07 — migration 003: maturity-aware monitoring reports.
-- Append-only history read by the API metric `promiseops_retraining_required`.
-- Owner: Ahmad (monitoring writer). Consumer: Armina (API metrics endpoint).

BEGIN;

CREATE TABLE IF NOT EXISTS monitoring_reports (
    report_id              bigserial PRIMARY KEY,
    as_of                  timestamptz NOT NULL,
    model_version          text NOT NULL CHECK (length(model_version) > 0),
    feature_window_start   timestamptz NOT NULL,
    feature_window_end     timestamptz NOT NULL,
    label_window_start     timestamptz NOT NULL,
    label_window_end       timestamptz NOT NULL,
    unique_orders          integer NOT NULL CHECK (unique_orders >= 0),
    mature_orders          integer NOT NULL CHECK (mature_orders >= 0),
    late_orders            integer NOT NULL CHECK (late_orders >= 0 AND late_orders <= mature_orders),
    drift_psi              jsonb NOT NULL DEFAULT '{}'::jsonb,
    categorical_tvd        jsonb NOT NULL DEFAULT '{}'::jsonb,
    persistent_drift       boolean NOT NULL DEFAULT false,
    recall_at_10           double precision,
    precision_at_10        double precision,
    average_precision      double precision,
    brier                  double precision,
    retraining_required    boolean NOT NULL DEFAULT false,
    reasons                jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT monitoring_reports_feature_window_order
        CHECK (feature_window_start <= feature_window_end),
    CONSTRAINT monitoring_reports_label_window_order
        CHECK (label_window_start <= label_window_end),
    -- A monitor-train retry writes the same report once: INSERT ... ON CONFLICT
    -- (model_version, as_of) DO NOTHING.
    CONSTRAINT monitoring_reports_model_as_of_key UNIQUE (model_version, as_of)
);

-- The API reads the latest report for the retraining gauge; this also drives the index order.
CREATE INDEX IF NOT EXISTS monitoring_reports_latest_idx
    ON monitoring_reports (as_of DESC, report_id DESC);

COMMIT;
