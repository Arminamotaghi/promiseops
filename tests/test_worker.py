import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyClassifier
from sklearn.pipeline import Pipeline

from promiseops import worker
from promiseops.features import FEATURES, cohort_hash
from promiseops.model import ApproachResult, Candidate, EvaluationMetrics
from promiseops.release import build_baseline_bundle, load_bundle, write_release_bundle

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

TRAIN_END = datetime(2017, 12, 31, 23, 59, 59, tzinfo=UTC)
VALIDATION_END = datetime(2018, 3, 31, 23, 59, 59, tzinfo=UTC)
AS_OF = datetime(2018, 5, 1, 0, 0, tzinfo=UTC)
VALIDATION_IDS = ["valid-00", "valid-01", "valid-02", "valid-03"]
COHORT_HASH = cohort_hash(VALIDATION_IDS)


class _FakeRun:
    def __init__(self, run_id: str = "run-123") -> None:
        self.info = SimpleNamespace(run_id=run_id)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeMlflow:
    def __init__(self, version: str = "3") -> None:
        self.calls = []
        self.version = version
        self.sklearn = SimpleNamespace(log_model=self._log_model)

    def _log_model(self, **kwargs):
        self.calls.append(("sklearn.log_model", kwargs))

    def set_tracking_uri(self, uri):
        self.calls.append(("set_tracking_uri", uri))

    def set_experiment(self, name):
        self.calls.append(("set_experiment", name))

    def start_run(self, run_name=None):
        self.calls.append(("start_run", run_name))
        return _FakeRun()

    def log_params(self, params):
        self.calls.append(("log_params", params))

    def log_metrics(self, metrics):
        self.calls.append(("log_metrics", metrics))

    def set_tags(self, tags):
        self.calls.append(("set_tags", tags))

    def log_artifacts(self, local_dir, artifact_path):
        self.calls.append(("log_artifacts", local_dir, artifact_path))

    def MlflowClient(self):
        version = self.version

        class Client:
            def search_model_versions(self, filter_string):
                return [SimpleNamespace(version=version)]

        return Client()


@pytest.fixture
def fake_mlflow(monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.test")
    return FakeMlflow()


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


def test_run_train_writes_a_bundle_that_load_bundle_accepts(tmp_path, monkeypatch, fake_mlflow):
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
        mlflow_module=fake_mlflow,
    )

    assert calls == [("connection", TRAIN_END, VALIDATION_END, AS_OF)]
    assert metadata["approach"] == "logistic_regression"
    assert metadata["model_version"] == (
        f"late-risk-logistic_regression.v3.{metadata['model_sha256'][:8]}"
    )
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
    assert metadata["mlflow"] == {
        "experiment": "promiseops-g07",
        "run_id": "run-123",
        "registered_model": "promiseops-late-risk",
        "version": "3",
    }
    assert ("set_tracking_uri", "http://mlflow.test") in fake_mlflow.calls
    assert ("set_experiment", "promiseops-g07") in fake_mlflow.calls
    assert any(call[0] == "log_params" for call in fake_mlflow.calls)
    assert any(call[0] == "log_metrics" for call in fake_mlflow.calls)
    assert ("log_artifacts", str(bundle_dir), "bundle") in fake_mlflow.calls
    assert metadata["data"]["feed_as_of"] == AS_OF.isoformat()

    bundle = load_bundle(bundle_dir)
    assert bundle.metadata == metadata
    sample = pd.DataFrame([{name: 0.0 for name in FEATURES}])
    probabilities = bundle.model.predict_proba(sample)
    assert probabilities.shape[0] == 1
    assert 0.0 <= float(probabilities[0, 0]) <= 1.0


def test_log_mlflow_requires_a_tracking_uri(tmp_path, monkeypatch):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)

    with pytest.raises(RuntimeError, match="MLFLOW_TRACKING_URI"):
        worker._log_mlflow(tmp_path, {}, object(), mlflow_module=FakeMlflow())


def test_run_train_fails_when_mlflow_logging_fails(tmp_path, monkeypatch, fake_mlflow):
    monkeypatch.setattr(worker, "train_candidate", lambda *args, **kwargs: _candidate())
    monkeypatch.setattr(worker, "current_source_commit", lambda: "a" * 40)

    def fail_log_params(params):
        raise RuntimeError("mlflow unavailable")

    fake_mlflow.log_params = fail_log_params

    with pytest.raises(RuntimeError, match="mlflow unavailable"):
        worker.run_train(
            "connection",
            train_end=TRAIN_END,
            validation_end=VALIDATION_END,
            as_of=AS_OF,
            out_dir=tmp_path / "release",
            mlflow_module=fake_mlflow,
        )


def test_run_train_compares_with_existing_incumbent_bundle(tmp_path, monkeypatch, fake_mlflow):
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
        mlflow_module=fake_mlflow,
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


def test_load_incumbent_ignores_the_bootstrap_bundle(tmp_path, monkeypatch):
    serving = tmp_path / "serving"
    build_baseline_bundle(serving, fixtures_dir=FIXTURES, source_commit="c" * 40)
    monkeypatch.setattr(worker, "INCUMBENT_DIR", serving)
    out_dir = tmp_path / "artifacts"

    assert worker._load_incumbent(out_dir) is None
    assert not (out_dir / "release.json").exists()


def test_run_train_gates_first_candidate_against_training_baseline(tmp_path, monkeypatch, fake_mlflow):
    serving = tmp_path / "serving"
    build_baseline_bundle(serving, fixtures_dir=FIXTURES, source_commit="c" * 40)
    monkeypatch.setattr(worker, "INCUMBENT_DIR", serving)
    monkeypatch.setattr(worker, "train_candidate", lambda *args, **kwargs: _candidate())
    monkeypatch.setattr(worker, "current_source_commit", lambda: "a" * 40)

    def fail_build_features(*args, **kwargs):
        raise AssertionError("the bootstrap bundle must not be scored as a champion")

    monkeypatch.setattr(worker, "build_features", fail_build_features)

    metadata = worker.run_train(
        "connection",
        train_end=TRAIN_END,
        validation_end=VALIDATION_END,
        as_of=AS_OF,
        out_dir=tmp_path / "artifacts",
        mlflow_module=fake_mlflow,
    )

    assert metadata["validation"]["champion"]["source"] == "baseline"
    assert metadata["gate"]["champion"] == "baseline"
    assert metadata["gate"]["decision"] == "accepted"


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
