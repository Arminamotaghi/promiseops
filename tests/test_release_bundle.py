import json
from pathlib import Path

import pandas as pd
import pytest
import sklearn

from promiseops.cli import main
from promiseops.features import FEATURE_CONTRACT_SHA256, FEATURES
from promiseops.release import (
    BundleError,
    build_baseline_bundle,
    load_bundle,
    sha256_file,
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
