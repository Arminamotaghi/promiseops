from datetime import UTC, date, datetime, time, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from promiseops.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    NUMERIC_FEATURES,
    cohort_hash,
)
from promiseops.model import (
    ApproachResult,
    EvaluationMetrics,
    build_models,
    compare_candidate,
    evaluate_predictions,
    load_training_frames,
    train_candidate,
)

TRAIN_END = datetime(2017, 12, 31, 23, 59, 59, tzinfo=UTC)
VALIDATION_END = datetime(2018, 3, 31, 23, 59, 59, tzinfo=UTC)
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


def _order(order_id, customer_id, approved_at, promised_date, late):
    promised = date.fromisoformat(promised_date)
    delivered_date = promised + (timedelta(days=1) if late else timedelta(0))
    delivered = datetime.combine(delivered_date, time(12, 0))
    return {
        "order": {
            "order_id": order_id,
            "customer_id": customer_id,
            "approved_at": _dt(approved_at),
            "promised_date": promised_date,
        },
        "outcome": {
            "order_id": order_id,
            "delivered_at": _dt(delivered),
            "available_at": _dt(delivered + timedelta(days=1)),
        },
    }


def _seed(connection) -> None:
    for statement in DDL.split(";"):
        statement = statement.strip()
        if statement:
            connection.execute(text(statement))

    connection.execute(
        text("INSERT INTO customers (customer_id, customer_state) VALUES (:customer_id, :customer_state)"),
        [
            {"customer_id": "c1", "customer_state": "SP"},
            {"customer_id": "c2", "customer_state": "RJ"},
        ],
    )
    connection.execute(
        text("INSERT INTO sellers (seller_id, seller_state) VALUES (:seller_id, :seller_state)"),
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
        ],
    )

    rows = []
    for index in range(8):
        approved = datetime(2017, index + 1, 1, 12, 0)
        promised = (approved.date() + timedelta(days=10)).isoformat()
        rows.append(
            _order(
                f"train-{index:02d}",
                "c1" if index % 2 == 0 else "c2",
                approved,
                promised,
                late=index % 2 == 0,
            )
        )
    rows.append(
        _order(
            "immature-01",
            "c1",
            datetime(2017, 12, 20, 12, 0),
            "2017-12-30",
            late=True,
        )
    )
    rows[-1]["outcome"]["available_at"] = _dt(datetime(2018, 1, 5, 12, 0))

    for index in range(4):
        approved = datetime(2018, 2, index + 1, 12, 0)
        promised = (approved.date() + timedelta(days=10)).isoformat()
        rows.append(
            _order(
                f"valid-{index:02d}",
                "c1" if index % 2 == 0 else "c2",
                approved,
                promised,
                late=index % 2 == 0,
            )
        )

    connection.execute(
        text(
            "INSERT INTO orders (order_id, customer_id, approved_at, promised_date) "
            "VALUES (:order_id, :customer_id, :approved_at, :promised_date)"
        ),
        [row["order"] for row in rows],
    )
    connection.execute(
        text(
            "INSERT INTO items (order_id, item_id, product_id, seller_id, price, freight) "
            "VALUES (:order_id, 1, :product_id, :seller_id, :price, :freight)"
        ),
        [
            {
                "order_id": row["order"]["order_id"],
                "product_id": "p1" if index % 2 == 0 else "p2",
                "seller_id": "s1" if index % 2 == 0 else "s2",
                "price": 100.0 if index % 2 == 0 else 50.0,
                "freight": 10.0 if index % 2 == 0 else 5.0,
            }
            for index, row in enumerate(rows)
        ],
    )
    connection.execute(
        text(
            "INSERT INTO payments (order_id, sequence, payment_type, installments, value) "
            "VALUES (:order_id, 1, :payment_type, :installments, :value)"
        ),
        [
            {
                "order_id": row["order"]["order_id"],
                "payment_type": "credit_card" if index % 2 == 0 else "voucher",
                "installments": 1,
                "value": 100.0 if index % 2 == 0 else 50.0,
            }
            for index, row in enumerate(rows)
        ],
    )
    connection.execute(
        text(
            "INSERT INTO outcomes (order_id, delivered_at, available_at) "
            "VALUES (:order_id, :delivered_at, :available_at)"
        ),
        [row["outcome"] for row in rows],
    )


@pytest.fixture()
def connection(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'model.db'}")
    with engine.begin() as setup:
        _seed(setup)
    with engine.connect() as conn:
        yield conn


def test_evaluate_predictions_uses_public_metrics():
    metrics = evaluate_predictions(
        [1, 1, 0, 0, 0, 0, 0, 0, 0, 0],
        [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0],
        [f"o{index}" for index in range(10)],
    )

    assert metrics.n == 10
    assert metrics.k == 1
    assert metrics.prevalence == 0.2
    assert metrics.recall_at_10 == 0.5
    assert metrics.precision_at_10 == 1.0
    assert metrics.average_precision == 1.0
    assert metrics.brier == pytest.approx(0.145)
    assert metrics.cohort_hash == cohort_hash([f"o{index}" for index in range(10)])


def test_evaluate_predictions_breaks_ties_by_order_id():
    metrics = evaluate_predictions(
        [0, 1, 0, 0, 0, 0, 0, 0, 0, 0],
        [0.9, 0.9, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
        ["b", "a", "c", "d", "e", "f", "g", "h", "i", "j"],
    )

    assert metrics.k == 1
    assert metrics.recall_at_10 == 1.0
    assert metrics.precision_at_10 == 1.0


def test_build_models_fit_and_evaluate():
    rows = []
    for index in range(40):
        row = {
            name: float((index * 7 + column) % 11)
            for column, name in enumerate(NUMERIC_FEATURES)
        }
        row.update(
            {
                name: "SP" if (index + column) % 2 == 0 else "RJ"
                for column, name in enumerate(CATEGORICAL_FEATURES)
            }
        )
        rows.append(row)

    frame = pd.DataFrame(rows, columns=list(FEATURES))
    labels = pd.Series([index % 2 for index in range(40)])
    train = frame.iloc[:30]
    validation = frame.iloc[30:]
    validation_labels = labels.iloc[30:]

    for model in build_models().values():
        model.fit(train, labels.iloc[:30])
        scores = model.predict_proba(validation)[:, 1]
        metrics = evaluate_predictions(
            validation_labels,
            scores,
            [f"o{index}" for index in range(30, 40)],
        )
        assert metrics.n == 10
        assert metrics.k == 1
        assert 0 <= metrics.recall_at_10 <= 1
        assert 0 <= metrics.precision_at_10 <= 1
        assert 0 <= metrics.average_precision <= 1
        assert 0 <= metrics.brier <= 1


def test_load_training_frames_excludes_immature_labels(connection):
    train, validation = load_training_frames(
        connection,
        train_end=TRAIN_END,
        validation_end=VALIDATION_END,
        as_of=AS_OF,
    )

    assert "immature-01" not in train.index
    assert len(train) == 8
    assert validation.index.tolist() == [
        "valid-00",
        "valid-01",
        "valid-02",
        "valid-03",
    ]


def test_train_candidate_returns_three_approaches(connection):
    candidate = train_candidate(
        connection,
        train_end=TRAIN_END,
        validation_end=VALIDATION_END,
        as_of=AS_OF,
    )

    assert set(candidate.results) == {
        "baseline",
        "logistic_regression",
        "hist_gradient_boosting",
    }
    assert candidate.validation_order_ids == [
        "valid-00",
        "valid-01",
        "valid-02",
        "valid-03",
    ]
    expected_hash = cohort_hash(candidate.validation_order_ids)
    for result in candidate.results.values():
        assert result.metrics.n == 4
        assert result.metrics.k == 1
        assert result.metrics.cohort_hash == expected_hash


def _metrics(**overrides) -> EvaluationMetrics:
    values = {
        "n": 10,
        "prevalence": 0.2,
        "k": 1,
        "recall_at_10": 0.5,
        "precision_at_10": 0.5,
        "average_precision": 0.4,
        "brier": 0.1,
        "cohort_hash": cohort_hash(["a", "b"]),
    }
    values.update(overrides)
    return EvaluationMetrics(**values)


def test_compare_candidate_applies_public_gates():
    champion = ApproachResult("champion", None, _metrics())
    accepted = ApproachResult(
        "candidate",
        None,
        _metrics(recall_at_10=0.6, average_precision=0.39, brier=0.104),
    )
    rejected = ApproachResult(
        "candidate",
        None,
        _metrics(recall_at_10=0.4, average_precision=0.3, brier=0.2),
    )

    assert compare_candidate(accepted, champion, ["a", "b"])["decision"] == "accepted"
    decision = compare_candidate(rejected, champion, ["a", "b"])
    assert decision["decision"] == "rejected"
    assert decision["reasons"]


def test_compare_candidate_rejects_different_cohorts():
    champion = ApproachResult("champion", None, _metrics())
    candidate = ApproachResult(
        "candidate",
        None,
        _metrics(cohort_hash=cohort_hash(["a", "c"])),
    )

    with pytest.raises(ValueError, match="cohort"):
        compare_candidate(candidate, champion, ["a", "b"])
