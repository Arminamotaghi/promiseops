from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from promiseops import worker
from promiseops.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES
from promiseops.monitor import _tvd, build_report

AS_OF = datetime(2018, 5, 1, 0, 0, tzinfo=UTC)


def _features(prefix: str, count: int, offset: float = 0.0) -> pd.DataFrame:
    values = np.linspace(0.0, 1.0, count) + offset
    data = {name: values for name in NUMERIC_FEATURES}
    data.update({name: "SP" for name in CATEGORICAL_FEATURES})
    return pd.DataFrame(data, index=[f"{prefix}{index:03d}" for index in range(count)])


def _predictions(prefix: str, count: int, score) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "risk_probability": [score(index) for index in range(count)],
            "model_version": "test-v1",
            "created_at": [AS_OF - timedelta(days=1)] * count,
        },
        index=[f"{prefix}{index:03d}" for index in range(count)],
    )


def _labels(prefix: str, count: int, limit: int | None = None) -> pd.DataFrame:
    late = [index % 2 == 0 for index in range(count)][:limit]
    return pd.DataFrame(
        {
            "late": late,
            "label_available_at": [AS_OF - timedelta(days=1)] * len(late),
        },
        index=[f"{prefix}{index:03d}" for index in range(len(late))],
    )


def test_insufficient_labels_suppresses_retraining():
    reference = {
        "features": _features("r", 120),
        "metrics": {"recall_at_10": 0.5, "average_precision": 0.5, "brier": 0.25},
    }
    report = build_report(
        _predictions("c", 120, lambda index: 0.9 if index % 2 == 0 else 0.1),
        _labels("c", 120, limit=99),
        reference,
        as_of=AS_OF,
        current_features=_features("c", 120),
        previous_features=_features("p", 120),
    )

    assert report["unique_orders"] == 120
    assert report["mature_orders"] == 99
    assert report["retraining_required"] is False
    assert any(reason["code"] == "insufficient_labels" for reason in report["reasons"])


def test_persistent_drift_with_mature_labels_requires_retraining():
    reference = {
        "features": _features("r", 120),
        "metrics": {"recall_at_10": 0.5, "average_precision": 0.5, "brier": 0.25},
    }
    report = build_report(
        _predictions("c", 120, lambda index: 0.9 if index % 2 == 0 else 0.1),
        _labels("c", 120),
        reference,
        as_of=AS_OF,
        current_features=_features("c", 120, offset=10.0),
        previous_features=_features("p", 120, offset=10.0),
    )

    assert report["persistent_drift"] is True
    assert report["retraining_required"] is True
    assert any(reason["code"] == "persistent_drift" for reason in report["reasons"])


def test_metric_degradation_against_reference_requires_retraining():
    reference = {
        "features": _features("r", 120),
        "metrics": {"recall_at_10": 1.0, "average_precision": 1.0, "brier": 0.01},
    }
    report = build_report(
        _predictions("c", 120, lambda index: 0.5),
        _labels("c", 120),
        reference,
        as_of=AS_OF,
        current_features=_features("c", 120),
        previous_features=_features("p", 120),
    )

    assert report["retraining_required"] is True
    codes = {reason["code"] for reason in report["reasons"]}
    assert "recall_degraded" in codes
    assert "average_precision_dropped" in codes
    assert "brier_worsened" in codes


def test_monitor_train_skips_training_when_report_does_not_require_it(tmp_path, monkeypatch):
    report = {
        "as_of": AS_OF,
        "model_version": "test-v1",
        "feature_window_start": AS_OF - timedelta(days=7),
        "feature_window_end": AS_OF,
        "label_window_start": AS_OF - timedelta(days=60),
        "label_window_end": AS_OF,
        "unique_orders": 120,
        "mature_orders": 120,
        "late_orders": 60,
        "drift_psi": {},
        "categorical_tvd": {},
        "persistent_drift": False,
        "recall_at_10": 1.0,
        "precision_at_10": 1.0,
        "average_precision": 1.0,
        "brier": 0.01,
        "retraining_required": False,
        "reasons": [],
    }
    written = []
    monkeypatch.setattr(
        worker,
        "_load_monitor_inputs",
        lambda *args, **kwargs: (
            pd.DataFrame(),
            pd.DataFrame(),
            {},
            pd.DataFrame(),
            pd.DataFrame(),
        ),
    )
    monkeypatch.setattr(worker, "build_report", lambda *args, **kwargs: report)
    monkeypatch.setattr(worker, "_write_monitor_report", lambda connection, value: written.append(value))
    monkeypatch.setattr(worker, "run_train", lambda *args, **kwargs: pytest.fail("must not train"))

    result = worker.monitor_train(
        "connection",
        train_end=AS_OF - timedelta(days=120),
        validation_end=AS_OF - timedelta(days=30),
        as_of=AS_OF,
        out_dir=tmp_path,
    )

    assert result is report
    assert written == [report]


def test_monitor_train_runs_train_when_report_requires_it(tmp_path, monkeypatch):
    report = {
        "as_of": AS_OF,
        "model_version": "test-v1",
        "feature_window_start": AS_OF - timedelta(days=7),
        "feature_window_end": AS_OF,
        "label_window_start": AS_OF - timedelta(days=60),
        "label_window_end": AS_OF,
        "unique_orders": 120,
        "mature_orders": 120,
        "late_orders": 60,
        "drift_psi": {},
        "categorical_tvd": {},
        "persistent_drift": True,
        "recall_at_10": 0.2,
        "precision_at_10": 0.2,
        "average_precision": 0.2,
        "brier": 0.25,
        "retraining_required": True,
        "reasons": [{"code": "persistent_drift", "feature": "item_count"}],
    }
    calls = []
    monkeypatch.setattr(
        worker,
        "_load_monitor_inputs",
        lambda *args, **kwargs: (
            pd.DataFrame(),
            pd.DataFrame(),
            {},
            pd.DataFrame(),
            pd.DataFrame(),
        ),
    )
    monkeypatch.setattr(worker, "build_report", lambda *args, **kwargs: report)
    monkeypatch.setattr(worker, "_write_monitor_report", lambda connection, value: None)
    monkeypatch.setattr(worker, "run_train", lambda *args, **kwargs: calls.append(kwargs))

    worker.monitor_train(
        "connection",
        train_end=AS_OF - timedelta(days=120),
        validation_end=AS_OF - timedelta(days=30),
        as_of=AS_OF,
        out_dir=tmp_path,
    )

    assert len(calls) == 1
    assert calls[0]["out_dir"] == tmp_path


def test_tvd_maps_missing_values_to_unknown():
    reference = pd.Series([np.nan, "SP", "SP"])
    current = pd.Series(["unknown", "SP", "SP"])

    assert _tvd(reference, current) == 0.0
