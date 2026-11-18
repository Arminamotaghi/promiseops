"""Maturity-aware monitoring report for the deployed model (Issue #13)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from promiseops.contracts import instant
from promiseops.features import CATEGORICAL_FEATURES, FEATURES, NUMERIC_FEATURES
from promiseops.model import evaluate_predictions

FEATURE_WINDOW = timedelta(days=7)
LABEL_WINDOW = timedelta(days=60)
MIN_UNIQUE_ORDERS = 100
MIN_MATURE_ORDERS = 100
DRIFT_THRESHOLD = 0.2
RANDOM_RECALL_BASELINE = 0.10
RECALL_RETENTION = 0.80
AP_DROP_LIMIT = 0.05
BRIER_WORSEN_LIMIT = 0.02
EPSILON = 1e-6


def _normalise_predictions(predictions: pd.DataFrame) -> pd.DataFrame:
    frame = predictions.copy()
    if "order_id" in frame.columns:
        frame = frame.set_index("order_id")
    if frame.index.has_duplicates:
        if "created_at" in frame.columns:
            frame = frame.sort_values("created_at").groupby(level=0, sort=True).tail(1)
        else:
            frame = frame.groupby(level=0, sort=True).tail(1)
    return frame.sort_index()


def _normalise_features(features: pd.DataFrame | None) -> pd.DataFrame:
    if features is None:
        return pd.DataFrame(columns=list(FEATURES))
    frame = features.copy()
    if "order_id" in frame.columns:
        frame = frame.set_index("order_id")
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame.sort_index()


def _normalise_labels(labels: pd.DataFrame | None) -> pd.DataFrame:
    if labels is None:
        return pd.DataFrame(columns=["late"])
    frame = labels.copy()
    if "order_id" in frame.columns:
        frame = frame.set_index("order_id")
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame.sort_index()


def _psi(reference: pd.Series, current: pd.Series) -> float:
    reference = pd.to_numeric(reference, errors="coerce").dropna()
    current = pd.to_numeric(current, errors="coerce").dropna()
    if reference.empty or current.empty:
        return 0.0
    quantiles = np.unique(np.quantile(reference, np.linspace(0.0, 1.0, 11)))
    if len(quantiles) < 2:
        return 0.0
    quantiles[0] = -np.inf
    quantiles[-1] = np.inf
    buckets = pd.IntervalIndex.from_breaks(quantiles)
    reference_counts = pd.cut(reference, bins=buckets, include_lowest=True).value_counts(
        normalize=True, sort=False
    )
    current_counts = pd.cut(current, bins=buckets, include_lowest=True).value_counts(
        normalize=True, sort=False
    )
    reference_share = reference_counts.reindex(buckets, fill_value=0.0).astype(float)
    current_share = current_counts.reindex(buckets, fill_value=0.0).astype(float)
    reference_share = reference_share.clip(lower=EPSILON)
    current_share = current_share.clip(lower=EPSILON)
    return float(np.sum((current_share - reference_share) * np.log(current_share / reference_share)))


def _tvd(reference: pd.Series, current: pd.Series) -> float:
    if reference.empty or current.empty:
        return 0.0
    reference = reference.fillna("unknown").astype(str)
    current = current.fillna("unknown").astype(str)
    categories = sorted(set(reference.unique()) | set(current.unique()))
    if not categories:
        return 0.0
    reference_share = reference.value_counts(normalize=True).reindex(categories, fill_value=0.0)
    current_share = current.value_counts(normalize=True).reindex(categories, fill_value=0.0)
    return float(0.5 * np.abs(current_share - reference_share).sum())


def _drift(reference: pd.DataFrame, current: pd.DataFrame, previous: pd.DataFrame) -> tuple[dict, dict, dict, dict]:
    numeric = {}
    categorical = {}
    current_drift = {}
    previous_drift = {}
    for name in NUMERIC_FEATURES:
        reference_values = reference[name] if name in reference else pd.Series(dtype=float)
        current_value = _psi(reference_values, current[name]) if name in current else 0.0
        previous_value = _psi(reference_values, previous[name]) if name in previous else 0.0
        numeric[name] = current_value
        if current_value > DRIFT_THRESHOLD:
            current_drift[name] = current_value
        if previous_value > DRIFT_THRESHOLD:
            previous_drift[name] = previous_value
    for name in CATEGORICAL_FEATURES:
        reference_values = reference[name] if name in reference else pd.Series(dtype=str)
        current_value = _tvd(reference_values, current[name]) if name in current else 0.0
        previous_value = _tvd(reference_values, previous[name]) if name in previous else 0.0
        categorical[name] = current_value
        if current_value > DRIFT_THRESHOLD:
            current_drift[name] = current_value
        if previous_value > DRIFT_THRESHOLD:
            previous_drift[name] = previous_value
    return numeric, categorical, current_drift, previous_drift


def build_report(
    predictions: pd.DataFrame,
    released_outcomes: pd.DataFrame,
    reference: dict | None,
    *,
    as_of: datetime,
    current_features: pd.DataFrame | None = None,
    previous_features: pd.DataFrame | None = None,
) -> dict:
    """Build one monitoring report from predictions, mature labels and a reference."""
    at = instant(as_of)
    predictions = _normalise_predictions(predictions)
    labels = _normalise_labels(released_outcomes)
    current = _normalise_features(current_features)
    previous = _normalise_features(previous_features)
    reference = reference or {}
    reference_features = _normalise_features(reference.get("features"))
    reference_metrics = reference.get("metrics") or {}

    reasons: list[dict] = []
    unique_orders = len(current)
    if unique_orders < MIN_UNIQUE_ORDERS:
        reasons.append(
            {"code": "insufficient_orders", "unique_orders": unique_orders, "required": MIN_UNIQUE_ORDERS}
        )

    mature = predictions.reindex(labels.index).dropna(subset=["risk_probability"])
    mature = mature.join(labels[[column for column in ("late", "label_available_at") if column in labels]], how="inner")
    metrics = None
    mature_orders = len(mature)
    late_orders = int(mature["late"].fillna(False).astype(bool).sum()) if mature_orders else 0
    sufficient_labels = (
        mature_orders >= MIN_MATURE_ORDERS
        and late_orders > 0
        and late_orders < mature_orders
    )
    if not sufficient_labels:
        reasons.append(
            {
                "code": "insufficient_labels",
                "mature_orders": mature_orders,
                "late_orders": late_orders,
                "required": MIN_MATURE_ORDERS,
            }
        )
    else:
        metrics = evaluate_predictions(
            mature["late"].astype(int),
            mature["risk_probability"].astype(float),
            mature.index.tolist(),
        )

    if reference_features.empty:
        numeric = {name: 0.0 for name in NUMERIC_FEATURES}
        categorical = {name: 0.0 for name in CATEGORICAL_FEATURES}
        current_drift = {}
        previous_drift = {}
    else:
        numeric, categorical, current_drift, previous_drift = _drift(
            reference_features, current, previous
        )
    persistent_drift = bool(
        set(current_drift) & set(previous_drift)
        and unique_orders >= MIN_UNIQUE_ORDERS
        and len(previous) >= MIN_UNIQUE_ORDERS
    )
    persistent_features = sorted(set(current_drift) & set(previous_drift))

    if persistent_drift:
        for name in persistent_features:
            reasons.append(
                {
                    "code": "persistent_drift",
                    "feature": name,
                    "current": round(current_drift[name], 6),
                    "previous": round(previous_drift[name], 6),
                    "threshold": DRIFT_THRESHOLD,
                }
            )

    if sufficient_labels:
        if metrics.recall_at_10 < RANDOM_RECALL_BASELINE:
            reasons.append(
                {
                    "code": "recall_below_random",
                    "value": metrics.recall_at_10,
                    "baseline": RANDOM_RECALL_BASELINE,
                }
            )
        reference_recall = reference_metrics.get("recall_at_10")
        if reference_recall is not None and metrics.recall_at_10 < RECALL_RETENTION * float(reference_recall):
            reasons.append(
                {
                    "code": "recall_degraded",
                    "value": metrics.recall_at_10,
                    "reference": float(reference_recall),
                    "retention": RECALL_RETENTION,
                }
            )
        reference_ap = reference_metrics.get("average_precision")
        if reference_ap is not None and metrics.average_precision < float(reference_ap) - AP_DROP_LIMIT:
            reasons.append(
                {
                    "code": "average_precision_dropped",
                    "value": metrics.average_precision,
                    "reference": float(reference_ap),
                    "limit": AP_DROP_LIMIT,
                }
            )
        reference_brier = reference_metrics.get("brier")
        if reference_brier is not None and metrics.brier > float(reference_brier) + BRIER_WORSEN_LIMIT:
            reasons.append(
                {
                    "code": "brier_worsened",
                    "value": metrics.brier,
                    "reference": float(reference_brier),
                    "limit": BRIER_WORSEN_LIMIT,
                }
            )

    retraining_required = bool(
        sufficient_labels
        and unique_orders >= MIN_UNIQUE_ORDERS
        and any(
            reason["code"]
            in {
                "persistent_drift",
                "recall_below_random",
                "recall_degraded",
                "average_precision_dropped",
                "brier_worsened",
            }
            for reason in reasons
        )
    )

    model_version = reference.get("model_version")
    if "model_version" in predictions and not predictions.empty:
        if "created_at" in predictions:
            latest = predictions.sort_values("created_at").iloc[-1]
            model_version = str(latest["model_version"])
        else:
            model_version = str(predictions["model_version"].iloc[-1])

    return {
        "as_of": at,
        "model_version": model_version or "unknown",
        "feature_window_start": at - FEATURE_WINDOW,
        "feature_window_end": at,
        "label_window_start": at - LABEL_WINDOW,
        "label_window_end": at,
        "unique_orders": unique_orders,
        "mature_orders": mature_orders,
        "late_orders": late_orders,
        "drift_psi": {name: round(value, 6) for name, value in numeric.items()},
        "categorical_tvd": {name: round(value, 6) for name, value in categorical.items()},
        "persistent_drift": persistent_drift,
        "recall_at_10": metrics.recall_at_10 if metrics else None,
        "precision_at_10": metrics.precision_at_10 if metrics else None,
        "average_precision": metrics.average_precision if metrics else None,
        "brier": metrics.brier if metrics else None,
        "retraining_required": retraining_required,
        "reasons": reasons,
    }


__all__ = ["FEATURE_WINDOW", "LABEL_WINDOW", "build_report"]
