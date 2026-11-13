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
from datetime import date, datetime

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


def build_features(
    connection,
    order_ids: Sequence[str] | None,
    *,
    as_of: datetime,
    labeled: bool = False,
):
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
    raise NotImplementedError("Ahmad: implement per docs/design/features-and-split.md (issue #9)")


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
