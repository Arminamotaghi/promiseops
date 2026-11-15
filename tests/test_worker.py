import json
from contextlib import contextmanager
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyClassifier
from sklearn.pipeline import Pipeline

from promiseops import worker
from promiseops.features import FEATURES, cohort_hash
from promiseops.model import ApproachResult, Candidate, EvaluationMetrics
from promiseops.release import load_bundle, write_release_bundle

TRAIN_END = datetime(2017, 12, 31, 23, 59, 59, tzinfo=UTC)
VALIDATION_END = datetime(2018, 3, 31, 23, 59, 59, tzinfo=UTC)
AS_OF = datetime(2018, 5, 1, 0, 0, tzinfo=UTC)
VALIDATION_IDS = ["valid-00", "valid-01", "valid-02", "valid-03"]
COHORT_HASH = cohort_hash(VALIDATION_IDS)


def _model() -> Pipeline:
    model = Pipeline([("model", DummyClassifier(strategy="prior"))])
    model.fit(np.zeros((4, len(FEATURES))), [0, 1, 0, 1])
    return model


def _metrics(
    *,
    recall: float = 0.5,
    precision: float = 1.0,
    average_precision: float = 0.5,
    brier: float = 0.25,
) -> EvaluationMetrics:
    return EvaluationMetrics(
        n=4,
        prevalence=0.5,
        k=1,
        recall_at_10=recall,
        precision_at_10=precision,
        average_precision=average_precision,
        brier=brier,
        cohort_hash=COHORT_HASH,
    )


def _incumbent_bundle(bundle_dir) -> dict:
    return write_release_bundle(
        bundle_dir,
        _model(),
        model_name="late-risk-incumbent",
        approach="logistic_regression",
        source_commit="b" * 40,
        cutoffs={"train_end": "2017-12-31T23:59:59+00:00"},
        validation=None,
        gate={"decision": "accepted", "reasons": []},
    )


def _candidate(**overrides: EvaluationMetrics) -> Candidate:
    metrics = {
        "baseline": _metrics(),
        "logistic_regression": _metrics(average_precision=0.6, brier=0.20),
        "hist_gradient_boosting": _metrics(recall=0.0, average_precision=0.9, brier=0.01),
    }
    metrics.update(overrides)
    return Candidate(
        train_order_ids=["train-00"],
        validation_order_ids=list(VALIDATION_IDS),
        results={
            name: ApproachResult(approach=name, model=_model(), metrics=value)
            for name, value in metrics.items()
        },
    )


def test_choose_approach_prefers_recall_over_average_precision():
    candidate = _candidate(
        baseline=_metrics(recall=0.5, average_precision=0.5),
        hist_gradient_boosting=_metrics(recall=0.4, average_precision=0.99, brier=0.0),
    )

    assert worker.choose_approach(candidate).approach == "logistic_regression"


def test_choose_approach_uses_average_precision_to_break_recall_ties():
    candidate = _candidate(
        baseline=_metrics(recall=0.5, average_precision=0.5),
        logistic_regression=_metrics(recall=0.5, average_precision=0.6),
        hist_gradient_boosting=_metrics(recall=0.5, average_precision=0.7),
    )

    assert worker.choose_approach(candidate).approach == "hist_gradient_boosting"


def test_choose_approach_uses_brier_to_break_metric_ties():
    candidate = _candidate(
        baseline=_metrics(recall=0.5, average_precision=0.5, brier=0.25),
        logistic_regression=_metrics(recall=0.5, average_precision=0.5, brier=0.20),
        hist_gradient_boosting=_metrics(recall=0.5, average_precision=0.5, brier=0.30),
    )

    assert worker.choose_approach(candidate).approach == "logistic_regression"


def test_run_train_writes_a_bundle_that_load_bundle_accepts(tmp_path, monkeypatch):
    candidate = _candidate()
    calls = []

    def fake_train_candidate(connection, *, train_end, validation_end, as_of):
        calls.append((connection, train_end, validation_end, as_of))
        return candidate

    monkeypatch.setattr(worker, "train_candidate", fake_train_candidate)
    monkeypatch.setattr(worker, "current_source_commit", lambda: "a" * 40)
    bundle_dir = tmp_path / "release"

    metadata = worker.run_train(
        "connection",
        train_end=TRAIN_END,
        validation_end=VALIDATION_END,
        as_of=AS_OF,
        out_dir=bundle_dir,
    )

    assert calls == [("connection", TRAIN_END, VALIDATION_END, AS_OF)]
    assert metadata["approach"] == "logistic_regression"
    assert metadata["model_version"].startswith("late-risk-logistic_regression.v0.")
    assert metadata["source_commit"] == "a" * 40
    assert metadata["cutoffs"] == {
        "train_end": TRAIN_END.isoformat(),
        "validation_end": VALIDATION_END.isoformat(),
        "as_of": AS_OF.isoformat(),
        "label_cutoff_train": TRAIN_END.isoformat(),
        "label_cutoff_validation": AS_OF.isoformat(),
    }
    assert metadata["validation"]["cohort_hash"] == COHORT_HASH
    assert metadata["validation"]["n"] == 4
    assert metadata["validation"]["k"] == 1
    assert set(metadata["validation"]["approaches"]) == {
        "baseline",
        "logistic_regression",
        "hist_gradient_boosting",
    }
    assert metadata["validation"]["champion"]["approach"] == "baseline"
    assert metadata["validation"]["champion"]["source"] == "baseline"
    assert metadata["gate"]["decision"] == "accepted"
    assert metadata["gate"]["champion"] == "baseline"
    assert metadata["mlflow"] is None
    assert metadata["data"]["feed_as_of"] == AS_OF.isoformat()

    bundle = load_bundle(bundle_dir)
    assert bundle.metadata == metadata
    sample = pd.DataFrame([{name: 0.0 for name in FEATURES}])
    probabilities = bundle.model.predict_proba(sample)
    assert probabilities.shape[0] == 1
    assert 0.0 <= float(probabilities[0, 0]) <= 1.0


def test_run_train_compares_with_existing_incumbent_bundle(tmp_path, monkeypatch):
    bundle_dir = tmp_path / "release"
    incumbent = _incumbent_bundle(bundle_dir)
    candidate = _candidate()

    monkeypatch.setattr(worker, "train_candidate", lambda *args, **kwargs: candidate)
    monkeypatch.setattr(worker, "current_source_commit", lambda: "a" * 40)

    def fake_build_features(connection, order_ids, *, as_of, labeled=False):
        return pd.DataFrame(
            {
                **{name: 0.0 for name in FEATURES},
                "late": [True, False, False, False],
            },
            index=pd.Index(VALIDATION_IDS, name="order_id"),
        )

    monkeypatch.setattr(worker, "build_features", fake_build_features)

    metadata = worker.run_train(
        "connection",
        train_end=TRAIN_END,
        validation_end=VALIDATION_END,
        as_of=AS_OF,
        out_dir=bundle_dir,
    )

    assert incumbent["model_version"].startswith("late-risk-incumbent.v0.")
    assert metadata["validation"]["champion"]["source"] == "incumbent"
    assert metadata["validation"]["champion"]["approach"].startswith("incumbent:")
    assert metadata["gate"]["champion"].startswith("incumbent:")
    assert metadata["gate"]["decision"] == "rejected"
    assert metadata["gate"]["reasons"] == ["recall_at_10 decreased"]


def test_load_incumbent_copies_the_serving_bundle(tmp_path, monkeypatch):
    serving = tmp_path / "serving"
    serving_metadata = _incumbent_bundle(serving)
    monkeypatch.setattr(worker, "INCUMBENT_DIR", serving)
    out_dir = tmp_path / "artifacts"

    bundle = worker._load_incumbent(out_dir)

    assert bundle is not None
    assert bundle.metadata == serving_metadata
    assert (out_dir / "release.json").is_file()
    assert (out_dir / "model.joblib").is_file()


def test_main_train_wires_environment_database_and_release_path(tmp_path, monkeypatch, capsys):
    captured = {}

    class FakeEngine:
        def __init__(self):
            self.disposed = False

        @contextmanager
        def connect(self):
            yield "connection"

        def dispose(self):
            self.disposed = True

    engine = FakeEngine()

    def fake_create_engine(url):
        captured["url"] = url
        return engine

    def fake_run_train(connection, *, train_end, validation_end, as_of, out_dir):
        captured.update(
            connection=connection,
            train_end=train_end,
            validation_end=validation_end,
            as_of=as_of,
            out_dir=out_dir,
        )
        return {"model_version": "late-risk-test.v0.12345678"}

    monkeypatch.setattr(worker, "create_engine", fake_create_engine)
    monkeypatch.setattr(worker, "run_train", fake_run_train)
    monkeypatch.setenv("PROMISEOPS_DATABASE_URL", "postgresql://promiseops@db/promiseops")
    monkeypatch.setenv("PROMISEOPS_RELEASE", str(tmp_path / "release.json"))

    exit_code = worker.main(
        [
            "--mode",
            "train",
            "--train-end",
            "2017-12-31T23:59:59Z",
            "--validation-end",
            "2018-03-31T23:59:59Z",
            "--as-of",
            "2018-05-01T00:00:00Z",
        ]
    )

    assert exit_code == 0
    assert captured["url"] == "postgresql+psycopg://promiseops@db/promiseops"
    assert captured["connection"] == "connection"
    assert captured["train_end"] == TRAIN_END
    assert captured["validation_end"] == VALIDATION_END
    assert captured["as_of"] == AS_OF
    assert captured["out_dir"] == tmp_path
    assert engine.disposed is True
    assert json.loads(capsys.readouterr().out) == {
        "model_version": "late-risk-test.v0.12345678"
    }


def test_database_url_requires_the_environment_variable(monkeypatch):
    monkeypatch.delenv("PROMISEOPS_DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="PROMISEOPS_DATABASE_URL"):
        worker._database_url()


def test_monitor_train_is_deferred_with_a_clean_failure(capsys):
    exit_code = worker.main(
        [
            "--mode",
            "monitor-train",
            "--train-end",
            "2017-12-31T23:59:59Z",
            "--validation-end",
            "2018-03-31T23:59:59Z",
            "--as-of",
            "2018-05-01T00:00:00Z",
        ]
    )

    assert exit_code == 2
    assert "monitor-train is not implemented yet" in capsys.readouterr().err
