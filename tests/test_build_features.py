from datetime import UTC, datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from promiseops.features import FEATURES, LABEL_COLUMNS, build_features

AS_OF = datetime(2018, 5, 1, 12, 0, tzinfo=UTC)

DDL = """
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    customer_state TEXT NOT NULL
);
CREATE TABLE sellers (
    seller_id TEXT PRIMARY KEY,
    seller_state TEXT NOT NULL
);
CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    weight_g REAL NOT NULL
);
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    promised_date TEXT NOT NULL
);
CREATE TABLE items (
    order_id TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    seller_id TEXT NOT NULL,
    price REAL NOT NULL,
    freight REAL NOT NULL,
    PRIMARY KEY (order_id, item_id)
);
CREATE TABLE payments (
    order_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    payment_type TEXT NOT NULL,
    installments INTEGER NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (order_id, sequence)
);
CREATE TABLE outcomes (
    order_id TEXT PRIMARY KEY,
    delivered_at TEXT NOT NULL,
    available_at TEXT NOT NULL
);
"""


def _dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _item(order_id, item_id, product_id, seller_id, price, freight):
    return {
        "order_id": order_id,
        "item_id": item_id,
        "product_id": product_id,
        "seller_id": seller_id,
        "price": price,
        "freight": freight,
    }


def _payment(order_id, sequence, payment_type, installments, value):
    return {
        "order_id": order_id,
        "sequence": sequence,
        "payment_type": payment_type,
        "installments": installments,
        "value": value,
    }


def _seed(connection) -> None:
    for statement in DDL.split(";"):
        statement = statement.strip()
        if statement:
            connection.execute(text(statement))

    connection.execute(
        text(
            "INSERT INTO customers (customer_id, customer_state) "
            "VALUES (:customer_id, :customer_state)"
        ),
        [
            {"customer_id": "c1", "customer_state": "SP"},
            {"customer_id": "c2", "customer_state": "RJ"},
        ],
    )
    connection.execute(
        text(
            "INSERT INTO sellers (seller_id, seller_state) "
            "VALUES (:seller_id, :seller_state)"
        ),
        [
            {"seller_id": "s1", "seller_state": "SP"},
            {"seller_id": "s2", "seller_state": "RJ"},
        ],
    )
    connection.execute(
        text(
            "INSERT INTO products (product_id, category, weight_g) "
            "VALUES (:product_id, :category, :weight_g)"
        ),
        [
            {"product_id": "p1", "category": "household", "weight_g": 700.0},
            {"product_id": "p2", "category": "toys", "weight_g": 300.0},
            {"product_id": "p3", "category": "books", "weight_g": 200.0},
        ],
    )
    connection.execute(
        text(
            "INSERT INTO orders (order_id, customer_id, approved_at, promised_date) "
            "VALUES (:order_id, :customer_id, :approved_at, :promised_date)"
        ),
        [
            {
                "order_id": "o1",
                "customer_id": "c1",
                "approved_at": _dt(datetime(2018, 1, 10, 1, 30)),
                "promised_date": "2018-01-09",
            },
            {
                "order_id": "o2",
                "customer_id": "c2",
                "approved_at": _dt(datetime(2018, 2, 1, 12, 0)),
                "promised_date": "2018-02-10",
            },
            {
                "order_id": "o3",
                "customer_id": "c1",
                "approved_at": _dt(datetime(2018, 6, 1, 12, 0)),
                "promised_date": "2018-06-10",
            },
            {
                "order_id": "o4",
                "customer_id": "c1",
                "approved_at": _dt(datetime(2018, 3, 1, 12, 0)),
                "promised_date": "2018-03-10",
            },
        ],
    )
    connection.execute(
        text(
            "INSERT INTO items (order_id, item_id, product_id, seller_id, price, freight) "
            "VALUES (:order_id, :item_id, :product_id, :seller_id, :price, :freight)"
        ),
        [
            _item("o1", 1, "p1", "s1", 100.0, 10.0),
            _item("o1", 2, "p2", "s2", 50.0, 5.0),
            _item("o2", 1, "p3", "s1", 70.0, 7.0),
            _item("o3", 1, "p1", "s1", 80.0, 8.0),
            _item("o4", 1, "p1", "s1", 50.0, 5.0),
            _item("o4", 2, "p2", "s2", 50.0, 5.0),
        ],
    )
    connection.execute(
        text(
            "INSERT INTO payments (order_id, sequence, payment_type, installments, value) "
            "VALUES (:order_id, :sequence, :payment_type, :installments, :value)"
        ),
        [
            _payment("o1", 1, "credit_card", 2, 100.0),
            _payment("o1", 2, "voucher", 0, 30.0),
            _payment("o1", 3, "debit_card", 1, 20.0),
            _payment("o2", 1, "credit_card", 1, 70.0),
            _payment("o3", 1, "credit_card", 1, 80.0),
            _payment("o4", 1, "credit_card", 1, 50.0),
            _payment("o4", 2, "voucher", 1, 50.0),
        ],
    )
    connection.execute(
        text(
            "INSERT INTO outcomes (order_id, delivered_at, available_at) "
            "VALUES (:order_id, :delivered_at, :available_at)"
        ),
        [
            {
                "order_id": "o1",
                "delivered_at": _dt(datetime(2018, 1, 10, 12, 0)),
                "available_at": _dt(datetime(2018, 1, 11, 12, 0)),
            },
            {
                "order_id": "o2",
                "delivered_at": _dt(datetime(2018, 2, 9, 12, 0)),
                "available_at": _dt(datetime(2018, 6, 1, 12, 0)),
            },
            {
                "order_id": "o4",
                "delivered_at": _dt(datetime(2018, 3, 9, 12, 0)),
                "available_at": _dt(datetime(2018, 3, 10, 12, 0)),
            },
        ],
    )


@pytest.fixture()
def connection(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'features.db'}")
    with engine.begin() as setup:
        _seed(setup)
    with engine.connect() as conn:
        yield conn


def test_two_items_and_three_payments_do_not_multiply_amounts(connection):
    frame = build_features(connection, ["o1"], as_of=AS_OF)
    row = frame.loc["o1"]

    assert frame.columns.tolist() == list(FEATURES)
    assert row["item_count"] == 2
    assert row["seller_count"] == 2
    assert row["total_price"] == 150
    assert row["total_freight"] == 15
    assert row["total_weight_g"] == 1000
    assert row["payment_total"] == 150
    assert row["max_installments"] == 2
    assert row["interstate_fraction"] == 0.5
    assert row["customer_state"] == "SP"
    assert row["primary_seller_state"] == "SP"
    assert row["primary_category"] == "household"
    assert row["primary_payment_type"] == "credit_card"


def test_primary_features_use_documented_tie_breaks(connection):
    row = build_features(connection, ["o4"], as_of=AS_OF).loc["o4"]

    assert row["primary_seller_state"] == "SP"
    assert row["primary_category"] == "household"
    assert row["primary_payment_type"] == "credit_card"


def test_local_time_features_use_sao_paulo(connection):
    row = build_features(connection, ["o1"], as_of=AS_OF).loc["o1"]

    assert row["promise_days"] == 0
    assert row["approval_hour"] == 23
    assert row["approval_weekday"] == 1


def test_labeled_true_excludes_labels_available_after_as_of(connection):
    frame = build_features(connection, ["o1", "o2", "o4"], as_of=AS_OF, labeled=True)

    assert frame.index.tolist() == ["o1", "o4"]
    assert frame.columns.tolist() == list(FEATURES) + list(LABEL_COLUMNS)
    assert bool(frame.loc["o1", "late"]) is True
    assert bool(frame.loc["o4", "late"]) is False
    assert frame.loc["o1", "label_available_at"] == pd.Timestamp("2018-01-11T12:00:00Z")
    assert frame["label_available_at"].dt.tz is not None


def test_as_of_excludes_future_orders(connection):
    frame = build_features(connection, None, as_of=AS_OF)

    assert frame.index.tolist() == ["o1", "o2", "o4"]
    assert "o3" not in frame.index


def test_empty_and_unknown_ids_return_empty_frames(connection):
    for order_ids in ([], ["missing"]):
        frame = build_features(connection, order_ids, as_of=AS_OF)
        assert frame.empty
        assert frame.columns.tolist() == list(FEATURES)
        assert frame.index.name == "order_id"


def test_output_is_deterministic(connection):
    first = build_features(connection, ["o1", "o4"], as_of=AS_OF)
    second = build_features(connection, ["o1", "o4"], as_of=AS_OF)

    pd.testing.assert_frame_equal(first, second)
