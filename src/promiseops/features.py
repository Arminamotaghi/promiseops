"""Shared feature contract: ONE code path for training and serving.

Owner: Ahmad (qbc13-g07-02). Contract finalized in MR !4 / issue #3; the
implementation of ``build_features`` lands in issue #9 (M2).

``contracts/features.json`` is the public source of truth for feature names and
order. This module keeps the same ordered tuple in code because the serving image
does not ship the contracts directory. ``tests/test_features_contract.py`` enforces
that the two stay identical.

Design and decisions: ``docs/design/features-and-split.md``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime

import pandas as pd
from sqlalchemy import bindparam, text

from promiseops.contracts import LOCAL_TZ, instant

# Order matters: it is the column order of the DataFrame and of the fitted pipeline.
NUMERIC_FEATURES: tuple[str, ...] = (
    "item_count",
    "seller_count",
    "total_price",
    "total_freight",
    "total_weight_g",
    "payment_total",
    "max_installments",
    "promise_days",
    "approval_hour",
    "approval_weekday",
    "interstate_fraction",
)
CATEGORICAL_FEATURES: tuple[str, ...] = (
    "customer_state",
    "primary_seller_state",
    "primary_category",
    "primary_payment_type",
)
FEATURES: tuple[str, ...] = NUMERIC_FEATURES + CATEGORICAL_FEATURES
LABEL_COLUMNS: tuple[str, ...] = ("late", "label_available_at")

# Raw columns are never model inputs. Only the documented derivatives in FEATURES are.
FORBIDDEN_INPUTS: frozenset[str] = frozenset(
    {
        "order_id",
        "customer_id",
        "approved_at",
        "promised_date",
        "delivered_at",
        "available_at",
        "late",
        "label_available_at",
        "released_at",
        "batch_id",
        "status",
        "review_score",
    }
)

LABEL = "late"


def local_date(value: datetime | str) -> date:
    """Calendar date in America/Sao_Paulo of an aware timestamp."""
    return instant(value).astimezone(LOCAL_TZ).date()


def is_late(delivered_at: datetime | str, promised_date: date | str) -> bool:
    """Target: local delivery DATE strictly after the promised DATE. Same day is on time."""
    promised = date.fromisoformat(promised_date) if isinstance(promised_date, str) else promised_date
    return local_date(delivered_at) > promised


def cohort_hash(order_ids: Sequence[str]) -> str:
    """Identity of an evaluation cohort: sha256 of the sorted unique IDs, newline separated."""
    joined = "\n".join(sorted(set(order_ids)))
    return hashlib.sha256(joined.encode()).hexdigest()


def feature_contract_sha256(names: Sequence[str] = FEATURES) -> str:
    """Hash of the ordered feature list; recorded in release.json and checked at API startup.

    Computed from the in-code tuple (the image does not ship contracts/); a test keeps the
    tuple equal to contracts/features.json. The canonical serialization is compact JSON:
    ``json.dumps(list(names), separators=(",", ":"))``.
    """
    return hashlib.sha256(json.dumps(list(names), separators=(",", ":")).encode()).hexdigest()


FEATURE_CONTRACT_SHA256: str = feature_contract_sha256()


_FEATURE_SQL = """
WITH eligible_orders AS (
    SELECT o.order_id, o.customer_id, o.approved_at, o.promised_date
    FROM orders o
    WHERE o.approved_at <= :as_of
    {order_filter}
),
item_agg AS (
    SELECT i.order_id,
           count(*) AS item_count,
           count(DISTINCT i.seller_id) AS seller_count,
           sum(i.price) AS total_price,
           sum(i.freight) AS total_freight,
           sum(p.weight_g) AS total_weight_g,
           avg(CASE WHEN s.seller_state <> c.customer_state
                    THEN 1.0 ELSE 0.0 END) AS interstate_fraction
    FROM items i
    JOIN eligible_orders e ON e.order_id = i.order_id
    JOIN products p ON p.product_id = i.product_id
    JOIN sellers s ON s.seller_id = i.seller_id
    JOIN customers c ON c.customer_id = e.customer_id
    GROUP BY i.order_id
),
pay_agg AS (
    SELECT p.order_id,
           sum(p.value) AS payment_total,
           max(p.installments) AS max_installments
    FROM payments p
    JOIN eligible_orders e ON e.order_id = p.order_id
    GROUP BY p.order_id
),
seller_totals AS (
    SELECT i.order_id, i.seller_id, s.seller_state, sum(i.price) AS seller_price
    FROM items i
    JOIN eligible_orders e ON e.order_id = i.order_id
    JOIN sellers s ON s.seller_id = i.seller_id
    GROUP BY i.order_id, i.seller_id, s.seller_state
),
seller_rank AS (
    SELECT seller_totals.*,
           row_number() OVER (
               PARTITION BY order_id
               ORDER BY seller_price DESC, seller_id ASC
           ) AS rn
    FROM seller_totals
),
category_totals AS (
    SELECT i.order_id, p.category, sum(i.price) AS category_price
    FROM items i
    JOIN eligible_orders e ON e.order_id = i.order_id
    JOIN products p ON p.product_id = i.product_id
    GROUP BY i.order_id, p.category
),
category_rank AS (
    SELECT category_totals.*,
           row_number() OVER (
               PARTITION BY order_id
               ORDER BY category_price DESC, category ASC
           ) AS rn
    FROM category_totals
),
payment_totals AS (
    SELECT p.order_id, p.payment_type, sum(p.value) AS payment_value
    FROM payments p
    JOIN eligible_orders e ON e.order_id = p.order_id
    GROUP BY p.order_id, p.payment_type
),
payment_rank AS (
    SELECT payment_totals.*,
           row_number() OVER (
               PARTITION BY order_id
               ORDER BY payment_value DESC, payment_type ASC
           ) AS rn
    FROM payment_totals
)
SELECT e.order_id, e.approved_at, e.promised_date, c.customer_state,
       ia.item_count, ia.seller_count, ia.total_price, ia.total_freight,
       ia.total_weight_g, pa.payment_total, pa.max_installments,
       ia.interstate_fraction,
       sr.seller_state AS primary_seller_state,
       cr.category AS primary_category,
       pr.payment_type AS primary_payment_type
       {label_columns}
FROM eligible_orders e
JOIN item_agg ia ON ia.order_id = e.order_id
JOIN pay_agg pa ON pa.order_id = e.order_id
JOIN customers c ON c.customer_id = e.customer_id
JOIN seller_rank sr ON sr.order_id = e.order_id AND sr.rn = 1
JOIN category_rank cr ON cr.order_id = e.order_id AND cr.rn = 1
JOIN payment_rank pr ON pr.order_id = e.order_id AND pr.rn = 1
{label_join}
ORDER BY e.order_id
"""


def _sql_datetime(value: datetime, connection) -> datetime | str:
    dialect = getattr(connection, "dialect", None)
    if dialect is not None and dialect.name == "sqlite":
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    return value


def _as_utc_datetime(value) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _empty_frame(labeled: bool) -> pd.DataFrame:
    columns = list(FEATURES) + (list(LABEL_COLUMNS) if labeled else [])
    return pd.DataFrame(columns=columns, index=pd.Index([], name="order_id"))


def _records_frame(records: list[dict], labeled: bool) -> pd.DataFrame:
    columns = list(FEATURES) + (list(LABEL_COLUMNS) if labeled else [])
    if not records:
        return _empty_frame(labeled)
    frame = pd.DataFrame.from_records(records).set_index("order_id")
    frame = frame[columns].sort_index()
    for name in NUMERIC_FEATURES:
        frame[name] = frame[name].astype(float)
    for name in CATEGORICAL_FEATURES:
        frame[name] = frame[name].astype(str)
    if labeled:
        frame["late"] = frame["late"].astype(bool)
        frame["label_available_at"] = pd.to_datetime(frame["label_available_at"], utc=True)
    return frame


def build_features(
    connection,
    order_ids: Sequence[str] | None,
    *,
    as_of: datetime,
    labeled: bool = False,
) -> pd.DataFrame:
    """Return a pandas.DataFrame indexed by order_id with columns == FEATURES (in that order).

    Contract:
      * only orders with ``approved_at <= as_of`` (and, if ``order_ids`` is given, only
        those IDs; unknown IDs are simply absent — the API turns that into 404)
      * ``items`` and ``payments`` are aggregated SEPARATELY per order_id, then joined to
        orders (never join items x payments: that multiplies amounts)
      * no column from ``outcomes`` is ever a feature
      * ``labeled=True`` adds ``LABEL_COLUMNS`` and keeps only orders whose
        ``outcome.available_at <= as_of``; the label is computed with :func:`is_late`
      * deterministic: same DB rows + same ``as_of`` => identical frame, sorted by order_id
      * ``connection`` is a SQLAlchemy 2.0 ``Connection`` (psycopg 3 driver); the caller owns
        the transaction. This function never commits.
    """
    as_of = instant(as_of)
    if order_ids is not None:
        if isinstance(order_ids, str):
            raise TypeError("order_ids must be a sequence of order IDs, not a string")
        order_ids = list(order_ids)
        if not order_ids:
            return _empty_frame(labeled)

    sql = _FEATURE_SQL.format(
        order_filter="AND o.order_id IN :order_ids" if order_ids is not None else "",
        label_columns=", oc.delivered_at, oc.available_at" if labeled else "",
        label_join=(
            "JOIN outcomes oc ON oc.order_id = e.order_id "
            "AND oc.available_at <= :as_of"
            if labeled
            else ""
        ),
    )
    statement = text(sql)
    params = {"as_of": _sql_datetime(as_of, connection)}
    if order_ids is not None:
        statement = statement.bindparams(bindparam("order_ids", expanding=True))
        params["order_ids"] = order_ids

    rows = connection.execute(statement, params).mappings().all()
    records = []
    for row in rows:
        approved_at = _as_utc_datetime(row["approved_at"])
        approved_local = approved_at.astimezone(LOCAL_TZ)
        promised_date = _as_date(row["promised_date"])
        record = {
            "order_id": row["order_id"],
            "item_count": float(row["item_count"]),
            "seller_count": float(row["seller_count"]),
            "total_price": float(row["total_price"]),
            "total_freight": float(row["total_freight"]),
            "total_weight_g": float(row["total_weight_g"]),
            "payment_total": float(row["payment_total"]),
            "max_installments": float(row["max_installments"]),
            "promise_days": float((promised_date - approved_local.date()).days),
            "approval_hour": float(approved_local.hour),
            "approval_weekday": float(approved_local.weekday()),
            "interstate_fraction": float(row["interstate_fraction"]),
            "customer_state": row["customer_state"],
            "primary_seller_state": row["primary_seller_state"],
            "primary_category": row["primary_category"],
            "primary_payment_type": row["primary_payment_type"],
        }
        if labeled:
            delivered_at = _as_utc_datetime(row["delivered_at"])
            record["late"] = bool(is_late(delivered_at, promised_date))
            record["label_available_at"] = _as_utc_datetime(row["available_at"])
        records.append(record)
    return _records_frame(records, labeled)


__all__ = [
    "CATEGORICAL_FEATURES",
    "FEATURES",
    "FEATURE_CONTRACT_SHA256",
    "FORBIDDEN_INPUTS",
    "LABEL",
    "LABEL_COLUMNS",
    "NUMERIC_FEATURES",
    "build_features",
    "cohort_hash",
    "feature_contract_sha256",
    "is_late",
    "local_date",
]
