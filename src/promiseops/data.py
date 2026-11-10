"""Own-team transactional ingestion; features live in one shared module."""

from promiseops.features import build_features


def ingest_batch(connection, payload, *, as_of, expected_sha256):
    """Return a receipt. Validate, quarantine invalid input, commit once, handle retry."""
    raise NotImplementedError("Implement the public ingestion contract")


__all__ = ["build_features", "ingest_batch"]
