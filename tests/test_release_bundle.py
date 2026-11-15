import json
from pathlib import Path
from types import SimpleNamespace

import joblib
import pandas as pd
import pytest
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from promiseops.cli import main
from promiseops.features import (
    CATEGORICAL_FEATURES,
    FEATURE_CONTRACT_SHA256,
    FEATURES,
    NUMERIC_FEATURES,
)
from promiseops.release import (
    BundleError,
    build_baseline_bundle,
    current_source_commit,
    load_bundle,
    sha256_file,
    write_release_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def _build(tmp_path: Path) -> tuple[Path, dict]:
    bundle_dir = tmp_path / "release"
    metadata = build_baseline_bundle(bundle_dir, fixtures_dir=FIXTURES)
    return bundle_dir, metadata


def test_make_baseline_bundle_passes_startup_checks(tmp_path):
    bundle_dir, metadata = _build(tmp_path)

    assert (bundle_dir / "release.json").is_file()
    assert (bundle_dir / "model.joblib").is_file()
    assert metadata["schema_version"] == "1.0"
    assert metadata["model_file"] == "model.joblib"
    assert metadata["features"] == list(FEATURES)
    assert metadata["feature_contract_sha256"] == FEATURE_CONTRACT_SHA256
    assert metadata["model_sha256"] == sha256_file(bundle_dir / "model.joblib")
    assert "bootstrap" in metadata["model_version"]
    assert metadata["software"]["scikit-learn"] == sklearn.__version__

    bundle = load_bundle(bundle_dir)
    assert bundle.metadata == metadata
    assert bundle.path == bundle_dir


def _real_encoder_pipeline() -> Pipeline:
    rows = []
    for index in range(4):
        row = {name: float(index + 1) for name in NUMERIC_FEATURES}
        row.update(
            {
                name: "SP" if index % 2 == 0 else "RJ"
                for name in CATEGORICAL_FEATURES
            }
        )
        rows.append(row)

    x_train = pd.DataFrame(rows, columns=list(FEATURES))
    y_train = [0, 1, 0, 1]
    preprocess = ColumnTransformer(
        [
            ("numeric", StandardScaler(), list(NUMERIC_FEATURES)),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore"),
                list(CATEGORICAL_FEATURES),
            ),
        ]
    )
    model = Pipeline(
        [
            ("preprocess", preprocess),
            ("model", LogisticRegression()),
        ]
    )
    model.fit(x_train, y_train)
    return model


def test_load_bundle_accepts_a_real_encoder_pipeline(tmp_path):
    bundle_dir, metadata = _build(tmp_path)
    joblib.dump(_real_encoder_pipeline(), bundle_dir / "model.joblib")
    model_sha256 = sha256_file(bundle_dir / "model.joblib")
    metadata["model_sha256"] = model_sha256
    metadata["model_version"] = f"late-risk-test.v0.{model_sha256[:8]}"
    (bundle_dir / "release.json").write_text(json.dumps(metadata))

    bundle = load_bundle(bundle_dir)

    assert bundle.model is not None


def test_baseline_probability_is_fixture_prevalence_in_unit_interval(tmp_path):
    bundle_dir, metadata = _build(tmp_path)
    bundle = load_bundle(bundle_dir)
    sample = pd.DataFrame([{name: 0.0 for name in FEATURES}])
    estimator = bundle.model.named_steps["model"]
    probabilities = bundle.model.predict_proba(sample)[0]
    probability = float(probabilities[list(estimator.classes_).index(1)])

    assert 0.0 <= probability <= 1.0
    assert probability == pytest.approx(metadata["bootstrap"]["prevalence"])


def test_cli_make_baseline_writes_a_valid_bundle(tmp_path):
    bundle_dir = tmp_path / "release"
    main(["make-baseline", "--out", str(bundle_dir), "--fixtures", str(FIXTURES)])

    assert (bundle_dir / "release.json").is_file()
    assert load_bundle(bundle_dir).metadata["model_version"].startswith("late-risk-bootstrap.")


def test_load_bundle_rejects_tampered_model(tmp_path):
    bundle_dir, _ = _build(tmp_path)
    (bundle_dir / "model.joblib").write_bytes(b"not a model")

    with pytest.raises(BundleError, match="model_sha256"):
        load_bundle(bundle_dir)


def test_load_bundle_rejects_wrong_feature_hash(tmp_path):
    bundle_dir, _ = _build(tmp_path)
    metadata = json.loads((bundle_dir / "release.json").read_text())
    metadata["feature_contract_sha256"] = "0" * 64
    (bundle_dir / "release.json").write_text(json.dumps(metadata))

    with pytest.raises(BundleError, match="feature_contract_sha256"):
        load_bundle(bundle_dir)


def test_load_bundle_rejects_unsafe_model_file(tmp_path):
    bundle_dir, _ = _build(tmp_path)
    metadata = json.loads((bundle_dir / "release.json").read_text())
    metadata["model_file"] = "../model.joblib"
    (bundle_dir / "release.json").write_text(json.dumps(metadata))

    with pytest.raises(BundleError, match="plain filename"):
        load_bundle(bundle_dir)


def _write_candidate(bundle_dir: Path, **overrides) -> dict:
    kwargs = {
        "model_name": "late-risk-test",
        "approach": "logistic_regression",
        "source_commit": "a" * 40,
        "cutoffs": {"train_end": "2017-12-31T23:59:59+00:00"},
        "validation": {"cohort_hash": "b" * 64, "n": 4},
        "gate": {"decision": "accepted", "reasons": []},
        "mlflow": {"run_id": "run-1", "experiment": "promiseops"},
        "data": {
            "schema_version": "1.0",
            "feed_as_of": "2018-05-01T00:00:00+00:00",
            "last_batch_id": "batch-1",
        },
    }
    kwargs.update(overrides)
    return write_release_bundle(bundle_dir, _real_encoder_pipeline(), **kwargs)


def test_write_release_bundle_passes_startup_checks(tmp_path):
    bundle_dir = tmp_path / "release"

    metadata = _write_candidate(bundle_dir)

    assert (bundle_dir / "release.json").is_file()
    assert (bundle_dir / "model.joblib").is_file()
    assert metadata["model_version"].startswith("late-risk-test.v0.")
    assert metadata["model_sha256"] == sha256_file(bundle_dir / "model.joblib")
    assert metadata["source_commit"] == "a" * 40
    assert metadata["approach"] == "logistic_regression"
    assert metadata["features"] == list(FEATURES)
    assert metadata["feature_contract_sha256"] == FEATURE_CONTRACT_SHA256
    assert metadata["software"]["scikit-learn"] == sklearn.__version__
    assert metadata["cutoffs"] == {"train_end": "2017-12-31T23:59:59+00:00"}
    assert metadata["validation"] == {"cohort_hash": "b" * 64, "n": 4}
    assert metadata["gate"] == {"decision": "accepted", "reasons": []}
    assert metadata["mlflow"] == {"run_id": "run-1", "experiment": "promiseops"}
    assert metadata["data"]["last_batch_id"] == "batch-1"

    bundle = load_bundle(bundle_dir)
    assert bundle.metadata == metadata
    assert bundle.model is not None


@pytest.mark.parametrize("source_commit", ["short", "z" * 40])
def test_write_release_bundle_rejects_invalid_source_commit(tmp_path, source_commit):
    with pytest.raises(BundleError, match="source_commit"):
        _write_candidate(tmp_path / "release", source_commit=source_commit)


def test_write_release_bundle_rejects_empty_model_name(tmp_path):
    with pytest.raises(BundleError, match="model_name"):
        _write_candidate(tmp_path / "release", model_name="  ")


def test_current_source_commit_prefers_ci_commit_sha(monkeypatch):
    monkeypatch.setenv("CI_COMMIT_SHA", "B" * 40)

    assert current_source_commit() == "b" * 40


def test_current_source_commit_falls_back_to_git(monkeypatch):
    monkeypatch.setenv("CI_COMMIT_SHA", "invalid")
    monkeypatch.setattr(
        "promiseops.release.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="c" * 40 + "\n"),
    )

    assert current_source_commit() == "c" * 40


def test_current_source_commit_fails_when_git_is_unavailable(monkeypatch):
    monkeypatch.delenv("CI_COMMIT_SHA", raising=False)

    def fail(*args, **kwargs):
        raise OSError("git is unavailable")

    monkeypatch.setattr("promiseops.release.subprocess.run", fail)

    with pytest.raises(BundleError, match="source commit is unavailable"):
        current_source_commit()


def test_current_source_commit_fails_for_invalid_git_output(monkeypatch):
    monkeypatch.delenv("CI_COMMIT_SHA", raising=False)
    monkeypatch.setattr(
        "promiseops.release.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="not-a-sha"),
    )

    with pytest.raises(BundleError, match="source commit is unavailable"):
        current_source_commit()
