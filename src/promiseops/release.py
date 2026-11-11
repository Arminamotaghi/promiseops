"""Build and validate the immutable model release bundle.

The bundle layout and startup checks are defined in ``docs/design/release.md`` and
``contracts/release.example.json``. The same validator is intended for the API
startup path so training and serving cannot drift.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.dummy import DummyClassifier
from sklearn.pipeline import Pipeline

from promiseops.features import FEATURE_CONTRACT_SHA256, FEATURES, is_late

RELEASE_SCHEMA_VERSION = "1.0"
DEFAULT_MODEL_FILE = "model.joblib"
RELEASE_FILE = "release.json"


class BundleError(ValueError):
    """A release bundle does not satisfy the startup contract."""


@dataclass(frozen=True)
class ReleaseBundle:
    path: Path
    metadata: dict
    model: object


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_commit(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _git_sha() -> str:
    from_environment = os.getenv("CI_COMMIT_SHA", "").strip().lower()
    if _is_commit(from_environment):
        return from_environment
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "0" * 40
    candidate = result.stdout.strip().lower()
    return candidate if _is_commit(candidate) else "0" * 40


def _fixture_labels(fixtures_dir: Path) -> tuple[list[str], list[int]]:
    orders_path = fixtures_dir / "synthetic-orders.json"
    outcomes_path = fixtures_dir / "synthetic-outcomes.json"
    try:
        orders_batch = json.loads(orders_path.read_text())
        outcomes_batch = json.loads(outcomes_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError(f"cannot read fixtures from {fixtures_dir}") from exc

    promised = {row["order_id"]: row["promised_date"] for row in orders_batch["orders"]}
    delivered = {row["order_id"]: row["delivered_at"] for row in outcomes_batch["outcomes"]}
    order_ids = sorted(promised)
    known_order_ids = [order_id for order_id in order_ids if order_id in delivered]
    if not known_order_ids:
        raise BundleError("fixtures contain no joined order/outcome rows")
    labels = [int(is_late(delivered[order_id], promised[order_id])) for order_id in known_order_ids]
    return known_order_ids, labels


def build_baseline_bundle(
    out_dir: Path | str = "release",
    *,
    fixtures_dir: Path | str = "fixtures",
    source_commit: str | None = None,
) -> dict:
    """Write a bootstrap bundle and return its release metadata.

    This model is deliberately a constant-prevalence baseline trained on the synthetic
    fixtures. It exists only so the API and staging path can be exercised before real
    data and a trained model are available. It must never be promoted to production.
    """
    out_dir = Path(out_dir)
    fixtures_dir = Path(fixtures_dir)
    order_ids, labels = _fixture_labels(fixtures_dir)
    if source_commit is None:
        commit = _git_sha()
    elif _is_commit(source_commit.strip().lower()):
        commit = source_commit.strip().lower()
    else:
        raise BundleError("source_commit must be 40 lowercase hexadecimal characters")

    x_train = pd.DataFrame(
        0.0,
        index=pd.Index(order_ids, name="order_id"),
        columns=list(FEATURES),
    )
    y_train = pd.Series(labels, index=x_train.index, dtype="int64")
    model = Pipeline([("model", DummyClassifier(strategy="prior"))])
    model.fit(x_train, y_train)

    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / DEFAULT_MODEL_FILE
    joblib.dump(model, model_path)
    model_sha256 = sha256_file(model_path)
    prevalence = float(sum(labels) / len(labels))
    metadata = {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "model_version": f"late-risk-bootstrap.v0.{model_sha256[:8]}",
        "model_file": DEFAULT_MODEL_FILE,
        "model_sha256": model_sha256,
        "approach": "dummy_prior_bootstrap",
        "features": list(FEATURES),
        "feature_contract_sha256": FEATURE_CONTRACT_SHA256,
        "source_commit": commit,
        "software": {
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "scikit-learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
        "mlflow": None,
        "data": {
            "schema_version": "1.0",
            "feed_as_of": None,
            "last_batch_id": None,
        },
        "cutoffs": {
            "train_end": None,
            "validation_end": None,
            "as_of": None,
            "label_cutoff_train": None,
            "label_cutoff_validation": None,
        },
        "validation": None,
        "gate": {
            "decision": "bootstrap",
            "reasons": ["path bootstrap only; never promoted to production"],
        },
        "bootstrap": {
            "fixtures": ["synthetic-orders.json", "synthetic-outcomes.json"],
            "n": len(labels),
            "prevalence": prevalence,
        },
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    (out_dir / RELEASE_FILE).write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")

    load_bundle(out_dir)
    return metadata


def _positive_probability(model: object, sample: pd.DataFrame) -> float:
    try:
        probabilities = np.asarray(model.predict_proba(sample))
    except Exception as exc:
        raise BundleError("model cannot produce predict_proba output") from exc
    classes = list(getattr(model, "classes_", []))
    if not classes:
        steps = getattr(model, "named_steps", None)
        if isinstance(steps, dict) and steps:
            classes = list(getattr(list(steps.values())[-1], "classes_", []))
    if not classes:
        raise BundleError("model does not expose classes_")
    if len(classes) == 1:
        probability = float(probabilities[0, 0]) if classes[0] == 1 else 0.0
    elif 1 in classes:
        probability = float(probabilities[0, classes.index(1)])
    else:
        raise BundleError("model has no positive class")
    if not np.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise BundleError("model probability is outside [0, 1]")
    return probability


def load_bundle(bundle_dir: Path | str = "release") -> ReleaseBundle:
    """Load a bundle and enforce every startup check from the release contract."""
    bundle_dir = Path(bundle_dir)
    release_path = bundle_dir / RELEASE_FILE
    try:
        metadata = json.loads(release_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError(f"cannot read {release_path}") from exc

    if metadata.get("schema_version") != RELEASE_SCHEMA_VERSION:
        raise BundleError("unsupported release schema_version")
    if not isinstance(metadata.get("model_version"), str) or not metadata["model_version"]:
        raise BundleError("model_version must be a non-empty string")
    if not _is_sha256(metadata.get("model_sha256")):
        raise BundleError("model_sha256 must be a sha256 hex digest")

    model_file = metadata.get("model_file")
    if not isinstance(model_file, str) or not model_file or Path(model_file).name != model_file:
        raise BundleError("model_file must be a plain filename inside the bundle")
    model_path = bundle_dir / model_file
    if not model_path.is_file():
        raise BundleError(f"missing model file: {model_file}")
    if sha256_file(model_path) != metadata["model_sha256"]:
        raise BundleError("model_sha256 does not match model file")

    if list(metadata.get("features", [])) != list(FEATURES):
        raise BundleError("release features do not match the shared feature contract")
    if metadata.get("feature_contract_sha256") != FEATURE_CONTRACT_SHA256:
        raise BundleError("feature_contract_sha256 does not match the code")

    software = metadata.get("software")
    if not isinstance(software, dict) or software.get("scikit-learn") != sklearn.__version__:
        raise BundleError("installed scikit-learn version does not match release metadata")

    try:
        model = joblib.load(model_path)
    except Exception as exc:
        raise BundleError("model file cannot be loaded") from exc

    sample = pd.DataFrame([{name: 0.0 for name in FEATURES}])
    _positive_probability(model, sample)
    return ReleaseBundle(path=bundle_dir, metadata=metadata, model=model)


__all__ = [
    "DEFAULT_MODEL_FILE",
    "RELEASE_FILE",
    "RELEASE_SCHEMA_VERSION",
    "BundleError",
    "ReleaseBundle",
    "build_baseline_bundle",
    "load_bundle",
    "sha256_file",
]
