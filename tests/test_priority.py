import pytest

from promiseops.priority import select_top_priority


def test_k_is_rounded_up():

    orders = [
        {"order_id": f"order-{i}", "risk_probability": i / 12}
        for i in range(1, 12)
    ]

    result = select_top_priority(orders)

    assert len(result) == 2


def test_tie_break_uses_ascending_order_id():

    orders = [
        {"order_id": "order-c", "risk_probability": 0.9},
        {"order_id": "order-a", "risk_probability": 0.9},
        {"order_id": "order-b", "risk_probability": 0.9},
        {"order_id": "order-d", "risk_probability": 0.1},
    ]

    result = select_top_priority(orders)

    assert [order["order_id"] for order in result] == ["order-a"]


def test_higher_risk_comes_first():

    orders = [
        {"order_id": "order-a", "risk_probability": 0.2},
        {"order_id": "order-b", "risk_probability": 0.9},
        {"order_id": "order-c", "risk_probability": 0.5},
    ]

    result = select_top_priority(orders)

    assert [order["order_id"] for order in result] == ["order-b"]


def test_empty_cohort_returns_empty_list():

    assert select_top_priority([]) == []


@pytest.mark.parametrize(
    "risk",
    [-0.1, 1.1, float("nan"), float("inf"), True, "0.5"],
)
def test_invalid_risk_probability_is_rejected(risk):

    orders = [{"order_id": "order-a", "risk_probability": risk}]

    with pytest.raises((ValueError, TypeError)):
        select_top_priority(orders)


def test_duplicate_order_ids_are_rejected():

    orders = [
        {"order_id": "order-a", "risk_probability": 0.9},
        {"order_id": "order-a", "risk_probability": 0.8},
    ]

    with pytest.raises((ValueError, TypeError)):
        select_top_priority(orders)


def test_build_priority_output_contains_contract_fields():
    from promiseops.priority import build_priority_output

    orders = [
        {"order_id": f"order-{i:02d}", "risk_probability": i / 12}
        for i in range(1, 12)
    ]

    result = build_priority_output(
        as_of="2026-11-10T00:00:00Z",
        batch_id="batch-001",
        cohort_id="cohort-001",
        model_version="model-001",
        scored_orders=orders,
    )

    assert result["schema_version"] == "1.0"
    assert result["as_of"] == "2026-11-10T00:00:00+00:00"
    assert result["batch_id"] == "batch-001"
    assert result["cohort_id"] == "cohort-001"
    assert result["model_version"] == "model-001"
    assert result["scored_count"] == 11
    assert len(result["top_orders"]) == 2
    assert result["top_orders"][0]["order_id"] == "order-11"


def test_tied_orders_inside_top_k_are_sorted_by_order_id():
    orders = [
        {"order_id": "order-z", "risk_probability": 0.9},
        {"order_id": "order-m", "risk_probability": 0.9},
        {"order_id": "order-a", "risk_probability": 0.9},
    ] + [
        {"order_id": f"order-{i:02d}", "risk_probability": 0.1}
        for i in range(18)
    ]

    result = select_top_priority(orders)

    assert len(result) == 3
    assert [order["order_id"] for order in result] == [
        "order-a",
        "order-m",
        "order-z",
    ]
