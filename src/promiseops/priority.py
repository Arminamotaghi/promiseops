"""Deterministic selection and contract output for batch priority."""

import hashlib
import math
from collections.abc import Iterable, Mapping

from promiseops.contracts import instant


def build_run_id(cohort_hash: str, model_version: str) -> str:
    """Build a deterministic run ID from cohort hash and model version."""
    if (
        not isinstance(cohort_hash, str)
        or len(cohort_hash) != 64
        or any(c not in "0123456789abcdef" for c in cohort_hash)
    ):
        raise ValueError("cohort_hash must be a lowercase SHA-256 hex digest")

    if not isinstance(model_version, str) or not model_version:
        raise ValueError("model_version must be a non-empty string")

    payload = f"{cohort_hash}{model_version}".encode()
    return hashlib.sha256(payload).hexdigest()


def select_top_priority(scored_orders: Iterable[Mapping]) -> list[dict]:
    """Select ceil(10% of the cohort), ordered by risk then order ID."""
    orders = list(scored_orders)
    seen_ids = set()

    for order in orders:
        if not isinstance(order, Mapping):
            raise TypeError("each scored order must be a mapping")
        order_id = order.get("order_id")
        risk = order.get("risk_probability")
        if not isinstance(order_id, str) or not order_id:
            raise ValueError("order_id must be a non-empty string")
        if order_id in seen_ids:
            raise ValueError(f"duplicate order_id: {order_id}")
        seen_ids.add(order_id)
        if isinstance(risk, bool) or not isinstance(risk, (int, float)):
            raise TypeError("risk_probability must be numeric")
        if not math.isfinite(risk) or not 0 <= risk <= 1:
            raise ValueError("risk_probability must be between 0 and 1")

    k = math.ceil(0.1 * len(orders))
    ordered = sorted(
        orders,
        key=lambda order: (-order["risk_probability"], order["order_id"]),
    )
    return [dict(order) for order in ordered[:k]]


def build_priority_output(
    *,
    as_of,
    cohort_id: str,
    cohort_rule: str,
    cohort_hash: str,
    model_version: str,
    scored_orders: Iterable[Mapping],
) -> dict:
    """Build the public batch-priority JSON contract."""
    for name, value in (
        ("cohort_id", cohort_id),
        ("cohort_rule", cohort_rule),
        ("cohort_hash", cohort_hash),
        ("model_version", model_version),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
    if len(cohort_hash) != 64 or any(c not in "0123456789abcdef" for c in cohort_hash):
        raise ValueError("cohort_hash must be a lowercase SHA-256 hex digest")

    normalized_as_of = instant(as_of).isoformat().replace("+00:00", "Z")
    orders = list(scored_orders)
    selected = select_top_priority(orders)
    k = max(1, math.ceil(0.1 * len(orders)))

    return {
        "schema_version": "1.0",
        "as_of": normalized_as_of,
        "cohort": {
            "id": cohort_id,
            "rule": cohort_rule,
            "hash": cohort_hash,
        },
        "model_version": model_version,
        "scored_count": len(orders),
        "k": k,
        "orders": [
            {
                "rank": rank,
                "order_id": order["order_id"],
                "risk_probability": order["risk_probability"],
            }
            for rank, order in enumerate(selected, start=1)
        ],
    }
