-- PromiseOps / g07 — migration 001: source tables + ingestion bookkeeping.
-- DRAFT CONTRACT (owner: Mohammadreza). Review in the MR before anyone codes against it.
--
-- Rules this schema encodes (docs/CONTRACTS.md):
--   * every source row is immutable; row_sha256 = promiseops.contracts.digest(row as dict)
--     lets ingestion tell "identical re-send" (allowed for dimensions) from "changed row" (reject)
--   * all timestamps are timestamptz in UTC; promised_date is a Brazilian local DATE
--   * one batch = one transaction = rows + its receipt, or nothing (then a quarantine row)
-- Apply with: psql "$PROMISEOPS_DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/001_source_and_ingest.sql
-- Re-running is safe (IF NOT EXISTS).

BEGIN;

-- ---------- dimensions ----------
CREATE TABLE IF NOT EXISTS customers (
    customer_id     text PRIMARY KEY,
    customer_state  char(2) NOT NULL CHECK (customer_state ~ '^[A-Z]{2}$'),
    row_sha256      char(64) NOT NULL,
    first_batch_id  text NOT NULL
);

CREATE TABLE IF NOT EXISTS sellers (
    seller_id       text PRIMARY KEY,
    seller_state    char(2) NOT NULL CHECK (seller_state ~ '^[A-Z]{2}$'),
    row_sha256      char(64) NOT NULL,
    first_batch_id  text NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id      text PRIMARY KEY,
    category        text NOT NULL CHECK (length(category) BETWEEN 1 AND 100),
    weight_g        double precision NOT NULL CHECK (weight_g >= 0 AND weight_g <= 1000000),
    row_sha256      char(64) NOT NULL,
    first_batch_id  text NOT NULL
);

-- ---------- facts ----------
CREATE TABLE IF NOT EXISTS orders (
    order_id        text PRIMARY KEY,
    customer_id     text NOT NULL REFERENCES customers (customer_id),
    approved_at     timestamptz NOT NULL,   -- knowledge time of the order itself (prediction time)
    promised_date   date NOT NULL,          -- America/Sao_Paulo local date
    row_sha256      char(64) NOT NULL,
    batch_id        text NOT NULL,
    released_at     timestamptz NOT NULL    -- batch.available_at: when the company feed published it
);
CREATE INDEX IF NOT EXISTS orders_approved_at_idx ON orders (approved_at);
CREATE INDEX IF NOT EXISTS orders_released_at_idx ON orders (released_at);

CREATE TABLE IF NOT EXISTS items (
    order_id        text NOT NULL REFERENCES orders (order_id),
    item_id         integer NOT NULL CHECK (item_id >= 1),
    product_id      text NOT NULL REFERENCES products (product_id),
    seller_id       text NOT NULL REFERENCES sellers (seller_id),
    price           double precision NOT NULL CHECK (price >= 0 AND price <= 10000000),
    freight         double precision NOT NULL CHECK (freight >= 0 AND freight <= 10000000),
    row_sha256      char(64) NOT NULL,
    PRIMARY KEY (order_id, item_id)
);

CREATE TABLE IF NOT EXISTS payments (
    order_id        text NOT NULL REFERENCES orders (order_id),
    sequence        integer NOT NULL CHECK (sequence >= 1),
    payment_type    text NOT NULL CHECK (length(payment_type) BETWEEN 1 AND 40),
    installments    integer NOT NULL CHECK (installments BETWEEN 0 AND 100),
    value           double precision NOT NULL CHECK (value >= 0 AND value <= 10000000),
    row_sha256      char(64) NOT NULL,
    PRIMARY KEY (order_id, sequence)
);

-- Labels. NEVER a feature. available_at = when the label became known (7-day reporting delay).
CREATE TABLE IF NOT EXISTS outcomes (
    order_id        text PRIMARY KEY REFERENCES orders (order_id),
    delivered_at    timestamptz NOT NULL,
    available_at    timestamptz NOT NULL CHECK (available_at >= delivered_at),
    row_sha256      char(64) NOT NULL,
    batch_id        text NOT NULL,
    released_at     timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS outcomes_available_at_idx ON outcomes (available_at);

-- ---------- ingestion bookkeeping ----------
-- One row per ACCEPTED batch, written in the same transaction as its rows.
CREATE TABLE IF NOT EXISTS batch_receipts (
    batch_id        text PRIMARY KEY,
    kind            text NOT NULL CHECK (kind IN ('orders', 'outcomes')),
    schema_version  text NOT NULL,
    available_at    timestamptz NOT NULL,
    payload_sha256  char(64) NOT NULL,      -- digest() of the parsed JSON (sorted keys)
    file_sha256     char(64) NOT NULL,      -- sha256 of the raw file bytes
    feed_as_of      timestamptz NOT NULL,   -- the as_of the DAG run used
    row_counts      jsonb NOT NULL,         -- {"orders": 812, "items": 950, ...}
    dag_run_id      text,
    ingested_at     timestamptz NOT NULL DEFAULT now()
);

-- Private quarantine for REJECTED attempts. Written in a separate transaction after rollback.
CREATE TABLE IF NOT EXISTS quarantine (
    quarantine_id   bigserial PRIMARY KEY,
    file_name       text NOT NULL,
    batch_id        text,                   -- may be NULL/unsafe if the envelope itself is broken
    payload_sha256  char(64),
    file_sha256     char(64) NOT NULL,
    reason_code     text NOT NULL CHECK (reason_code ~ '^[a-z0-9_]{3,64}$'),  -- e.g. checksum_mismatch, schema_invalid, missing_fk ...
    reason_detail   text NOT NULL,          -- human readable, NO secrets
    raw_payload     bytea NOT NULL,         -- exact bytes we received
    feed_as_of      timestamptz,
    dag_run_id      text,
    received_at     timestamptz NOT NULL DEFAULT now()
);

COMMIT;

-- OPEN QUESTIONS for the MR (Mohammadreza decides, team reviews):
--  1. An identical ORDER row re-sent under a different batch_id: no-op or reject?
--     (CONTRACTS only says identical DIMENSION rows are reusable.)
-- Answer 1. REPEAT ORDER_ID IN DIFFERENT BATCH:
--   - If the entire order (including all associated items and payments) is identical 
--     to a previously committed one (matching row_sha256): No-op (Idempotent success).
--   - If an order_id is re-sent but with DIFFERENT or ADDITIONAL items/payments: 
--     Reject with 'committed_order_extended' error and move to quarantine. 
--     (Reason: Facts are immutable; once committed, a transaction cannot be extended).
--     This logic will be enforced in the application layer (ingest_batch task #7).


--  2. Should rejected re-tries of the same broken file add a second quarantine row,
--     or reuse the first one (unique on file_sha256 + reason_code)?
-- Answer 2. RETRY MECHANISM:
-- Rationale: Based on ASSESSMENT.fa.md (Audit trail requirements), failure history must be explicit.
--     - Implementation:
--          Each failed ingestion attempt must be distinct for auditing.
--          Retrying a failed file creates a new unique record in the 'quarantine' table to 
--          maintain a full audit trail of failure history, allowing monitoring systems to track 
--          retry frequency per file.
