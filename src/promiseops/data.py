"""Own-team transactional ingestion and a single leakage-safe feature builder."""


def ingest_batch(connection, payload, *, as_of, expected_sha256):
    """Return a receipt. Validate, quarantine invalid input, commit once, handle retry."""
    raise NotImplementedError("Implement the public ingestion contract")


def build_features(connection, order_ids, *, as_of, labeled=False):
    """Aggregate one-to-many relations independently; never use outcome fields as features."""
    raise NotImplementedError("Implement shared training/serving features")
