"""Training, evaluation and candidate/champion gates for PromiseOps."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sqlalchemy import text

from promiseops.contracts import instant
from promiseops.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    NUMERIC_FEATURES,
    build_features,
    cohort_hash,
)

GATE_AP_DROP = 0.02
GATE_BRIER_WORSEN = 0.005


@dataclass(frozen=True)
class EvaluationMetrics:
    n: int
    prevalence: float
    k: int
    recall_at_10: float
    precision_at_10: float
    average_precision: float
    brier: float
    cohort_hash: str


@dataclass
class ApproachResult:
    approach: str
    model: Any
    metrics: EvaluationMetrics


@dataclass
class Candidate:
    train_order_ids: list[str]
    validation_order_ids: list[str]
    results: dict[str, ApproachResult]


def _sql_datetime(value: datetime, connection) -> datetime | str:
    dialect = getattr(connection, "dialect", None)
    if dialect is not None and dialect.name == "sqlite":
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    return value


def _preprocessor() -> ColumnTransformer:
    return ColumnTransformer(
        [
            ("numeric", StandardScaler(), list(NUMERIC_FEATURES)),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                list(CATEGORICAL_FEATURES),
            ),
        ]
    )


def _pipeline(estimator) -> Pipeline:
    return Pipeline([("preprocess", _preprocessor()), ("model", estimator)])


def build_models() -> dict[str, Pipeline]:
    """Return the three approaches required by the model card."""
    return {
        "baseline": Pipeline([("model", DummyClassifier(strategy="prior"))]),
        "logistic_regression": _pipeline(
            LogisticRegression(max_iter=1000, random_state=42)
        ),
        "hist_gradient_boosting": _pipeline(
            HistGradientBoostingClassifier(random_state=42)
        ),
    }


def _positive_scores(model, features: pd.DataFrame) -> np.ndarray:
    probabilities = np.asarray(model.predict_proba(features))
    classes = list(getattr(model, "classes_", []))
    if not classes:
        steps = getattr(model, "named_steps", None)
        if isinstance(steps, dict) and steps:
            classes = list(getattr(list(steps.values())[-1], "classes_", []))
    if len(classes) == 1:
        return (
            np.ones(len(features), dtype=float)
            if classes[0] == 1
            else np.zeros(len(features), dtype=float)
        )
    return probabilities[:, classes.index(1)].astype(float)


def evaluate_predictions(
    y_true,
    scores,
    order_ids,
) -> EvaluationMetrics:
    """Compute the public ranking and probability metrics on one cohort."""
    labels = np.asarray(y_true, dtype=int)
    probabilities = np.asarray(scores, dtype=float)
    ids = list(order_ids)
    if len(labels) != len(probabilities) or len(labels) != len(ids):
        raise ValueError("labels, scores and order_ids must have the same length")
    if len(labels) == 0:
        raise ValueError("cohort is empty")
    positives = int(labels.sum())
    if positives == 0 or positives == len(labels):
        raise ValueError("cohort must contain both classes")

    k = max(1, math.ceil(0.1 * len(labels)))
    ranked = pd.DataFrame(
        {"order_id": ids, "label": labels, "score": probabilities}
    ).sort_values(["score", "order_id"], ascending=[False, True])
    top = ranked.head(k)
    return EvaluationMetrics(
        n=len(labels),
        prevalence=positives / len(labels),
        k=k,
        recall_at_10=float(top["label"].sum() / positives),
        precision_at_10=float(top["label"].sum() / k),
        average_precision=float(average_precision_score(labels, probabilities)),
        brier=float(brier_score_loss(labels, probabilities)),
        cohort_hash=cohort_hash(ids),
    )


def load_training_frames(
    connection,
    *,
    train_end: datetime,
    validation_end: datetime,
    as_of: datetime,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load mature train labels and the validation cohort inside the documented window."""
    train_end = instant(train_end)
    validation_end = instant(validation_end)
    as_of = instant(as_of)
    if not train_end < validation_end <= as_of:
        raise ValueError("require train_end < validation_end <= as_of")

    train = build_features(connection, None, as_of=train_end, labeled=True)
    if train.empty:
        raise ValueError("no mature training orders")

    statement = text(
        "SELECT order_id FROM orders "
        "WHERE approved_at > :train_end AND approved_at <= :validation_end "
        "ORDER BY order_id"
    )
    params = {
        "train_end": _sql_datetime(train_end, connection),
        "validation_end": _sql_datetime(validation_end, connection),
    }
    validation_ids = [row[0] for row in connection.execute(statement, params)]
    if not validation_ids:
        raise ValueError("no validation orders in window")

    validation = build_features(
        connection,
        validation_ids,
        as_of=as_of,
        labeled=True,
    )
    if validation.empty:
        raise ValueError("no validation orders with mature labels")
    return train, validation


def train_candidate(
    connection,
    *,
    train_end: datetime,
    validation_end: datetime,
    as_of: datetime,
) -> Candidate:
    """Fit the three approaches and evaluate them on the same validation cohort."""
    train, validation = load_training_frames(
        connection,
        train_end=train_end,
        validation_end=validation_end,
        as_of=as_of,
    )
    train_features = train[list(FEATURES)]
    train_labels = train["late"].astype(int)
    validation_features = validation[list(FEATURES)]
    validation_labels = validation["late"].astype(int)
    validation_ids = validation.index.tolist()

    results = {}
    for name, model in build_models().items():
        model.fit(train_features, train_labels)
        scores = _positive_scores(model, validation_features)
        results[name] = ApproachResult(
            approach=name,
            model=model,
            metrics=evaluate_predictions(
                validation_labels,
                scores,
                validation_ids,
            ),
        )
    return Candidate(
        train_order_ids=train.index.tolist(),
        validation_order_ids=validation_ids,
        results=results,
    )


def compare_candidate(candidate: ApproachResult, champion: ApproachResult, cohort) -> dict:
    """Apply the public gates on an identical validation cohort."""
    expected_hash = cohort_hash(list(cohort))
    if candidate.metrics.cohort_hash != expected_hash:
        raise ValueError("candidate metrics use a different cohort")
    if champion.metrics.cohort_hash != expected_hash:
        raise ValueError("champion metrics use a different cohort")

    reasons = []
    if candidate.metrics.recall_at_10 < champion.metrics.recall_at_10:
        reasons.append("recall_at_10 decreased")
    if candidate.metrics.average_precision < champion.metrics.average_precision - GATE_AP_DROP:
        reasons.append("average_precision dropped more than 0.02")
    if candidate.metrics.brier > champion.metrics.brier + GATE_BRIER_WORSEN:
        reasons.append("brier worsened more than 0.005")

    return {
        "decision": "accepted" if not reasons else "rejected",
        "reasons": reasons,
        "cohort_hash": expected_hash,
        "candidate": candidate.approach,
        "champion": champion.approach,
    }


__all__ = [
    "ApproachResult",
    "Candidate",
    "EvaluationMetrics",
    "build_models",
    "compare_candidate",
    "evaluate_predictions",
    "load_training_frames",
    "train_candidate",
]
