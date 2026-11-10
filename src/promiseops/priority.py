"""Deterministic selection of the highest-risk orders."""

import math
from collections.abc import Iterable, Mapping


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
    batch_id: str,
    cohort_id: str,
    model_version: str,
    scored_orders: Iterable[Mapping],
) -> dict:
    """Build the public, deterministic batch-priority output."""

    from promiseops.contracts import instant

    if not isinstance(batch_id, str) or not batch_id:
        raise ValueError("batch_id must be a non-empty string")

    if not isinstance(cohort_id, str) or not cohort_id:
        raise ValueError("cohort_id must be a non-empty string")

    if not isinstance(model_version, str) or not model_version:
        raise ValueError("model_version must be a non-empty string")

    normalized_as_of = instant(as_of).isoformat()
    orders = list(scored_orders)
    top_orders = select_top_priority(orders)

    return {
        "schema_version": "1.0",
        "as_of": normalized_as_of,
        "batch_id": batch_id,
        "cohort_id": cohort_id,
        "model_version": model_version,
        "scored_count": len(orders),
        "top_orders": [
            {
                "order_id": order["order_id"],
                "risk_probability": order["risk_probability"],
            }
            for order in top_orders
        ],
    }
