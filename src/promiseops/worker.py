"""Queued worker entry point invoked by the staff's bounded Job dispatcher."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine

from promiseops.contracts import instant
from promiseops.features import FEATURES, build_features
from promiseops.model import (
    ApproachResult,
    Candidate,
    EvaluationMetrics,
    compare_candidate,
    evaluate_predictions,
    train_candidate,
)
from promiseops.release import (
    RELEASE_FILE,
    BundleError,
    ReleaseBundle,
    current_source_commit,
    load_bundle,
    write_release_bundle,
    write_release_metadata,
)

INCUMBENT_DIR = Path("/opt/promiseops/release")
MLFLOW_EXPERIMENT = "promiseops-g07"
MLFLOW_REGISTERED_MODEL = "promiseops-late-risk"


def choose_approach(candidate: Candidate) -> ApproachResult:
    """Pick the candidate with the best public ranking metrics, then Brier."""
    return max(
        candidate.results.values(),
        key=lambda result: (
            result.metrics.recall_at_10,
            result.metrics.average_precision,
            -result.metrics.brier,
        ),
    )


def _positive_scores(model, features) -> np.ndarray:
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


def _metrics_dict(metrics) -> dict:
    return {
        "n": metrics.n,
        "prevalence": metrics.prevalence,
        "k": metrics.k,
        "recall_at_10": metrics.recall_at_10,
        "precision_at_10": metrics.precision_at_10,
        "average_precision": metrics.average_precision,
        "brier": metrics.brier,
        "cohort_hash": metrics.cohort_hash,
    }


def _validation_payload(
    candidate: Candidate,
    chosen: ApproachResult,
    champion: ApproachResult,
    champion_source: str,
) -> dict:
    return {
        "cohort_hash": chosen.metrics.cohort_hash,
        "n": chosen.metrics.n,
        "prevalence": chosen.metrics.prevalence,
        "k": chosen.metrics.k,
        "recall_at_10": chosen.metrics.recall_at_10,
        "precision_at_10": chosen.metrics.precision_at_10,
        "average_precision": chosen.metrics.average_precision,
        "brier": chosen.metrics.brier,
        "champion": {
            "approach": champion.approach,
            "source": champion_source,
            "metrics": _metrics_dict(champion.metrics),
        },
        "approaches": {
            name: _metrics_dict(result.metrics)
            for name, result in candidate.results.items()
        },
    }


def _cutoffs(
    train_end: datetime,
    validation_end: datetime,
    as_of: datetime,
) -> dict:
    return {
        "train_end": train_end.isoformat(),
        "validation_end": validation_end.isoformat(),
        "as_of": as_of.isoformat(),
        "label_cutoff_train": train_end.isoformat(),
        "label_cutoff_validation": as_of.isoformat(),
    }


def _is_bootstrap(bundle: ReleaseBundle) -> bool:
    """True for the path-only bundle the image carries before any trained release."""
    gate = bundle.metadata.get("gate")
    return isinstance(gate, dict) and gate.get("decision") == "bootstrap"


def _load_incumbent(out_dir: Path) -> ReleaseBundle | None:
    for bundle_dir in (out_dir, INCUMBENT_DIR):
        if not (bundle_dir / RELEASE_FILE).is_file():
            continue
        try:
            bundle = load_bundle(bundle_dir)
        except (BundleError, OSError):
            continue
        if _is_bootstrap(bundle):
            # Not a trained champion: the first candidate is gated against the
            # training-prevalence baseline instead (docs/CONTRACTS.md, evaluation and release).
            continue
        if bundle_dir != out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundle_dir / RELEASE_FILE, out_dir / RELEASE_FILE)
            shutil.copy2(
                bundle_dir / bundle.metadata["model_file"],
                out_dir / bundle.metadata["model_file"],
            )
        return bundle
    return None


def _incumbent_metrics(
    connection,
    bundle: ReleaseBundle,
    candidate: Candidate,
    *,
    as_of: datetime,
) -> EvaluationMetrics:
    validation = build_features(
        connection,
        candidate.validation_order_ids,
        as_of=as_of,
        labeled=True,
    )
    if validation.empty or set(validation.index) != set(candidate.validation_order_ids):
        raise ValueError("incumbent validation cohort does not match candidate cohort")
    validation = validation.reindex(candidate.validation_order_ids)
    scores = _positive_scores(bundle.model, validation[list(FEATURES)])
    return evaluate_predictions(
        validation["late"].astype(int),
        scores,
        candidate.validation_order_ids,
    )


def _log_mlflow(
    out_dir: Path,
    metadata: dict,
    model,
    *,
    mlflow_module=None,
) -> dict:
    """Log one training run and the exact bundle to the team MLflow server."""
    if mlflow_module is None:
        import mlflow as mlflow_module
        import mlflow.sklearn

    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        raise RuntimeError("MLFLOW_TRACKING_URI is not configured")

    experiment = MLFLOW_EXPERIMENT
    registered_model = MLFLOW_REGISTERED_MODEL
    validation = metadata["validation"]
    cutoffs = metadata["cutoffs"]

    mlflow_module.set_tracking_uri(tracking_uri)
    mlflow_module.set_experiment(experiment)
    with mlflow_module.start_run(
        run_name=f"late-risk-{metadata['approach']}-{metadata['model_sha256'][:8]}"
    ) as run:
        run_id = run.info.run_id
        mlflow_module.log_params(
            {
                "approach": metadata["approach"],
                "model_sha256": metadata["model_sha256"],
                "source_commit": metadata["source_commit"],
                "feature_contract_sha256": metadata["feature_contract_sha256"],
                "cohort_hash": validation["cohort_hash"],
                "n": validation["n"],
                "k": validation["k"],
                "train_end": cutoffs["train_end"],
                "validation_end": cutoffs["validation_end"],
                "as_of": cutoffs["as_of"],
                "label_cutoff_train": cutoffs["label_cutoff_train"],
                "label_cutoff_validation": cutoffs["label_cutoff_validation"],
            }
        )
        mlflow_module.log_metrics(
            {
                "n": validation["n"],
                "prevalence": validation["prevalence"],
                "k": validation["k"],
                "recall_at_10": validation["recall_at_10"],
                "precision_at_10": validation["precision_at_10"],
                "average_precision": validation["average_precision"],
                "brier": validation["brier"],
            }
        )
        mlflow_module.set_tags(
            {
                "approach": metadata["approach"],
                "gate_decision": metadata["gate"]["decision"],
                "champion": metadata["gate"]["champion"],
            }
        )
        mlflow_module.sklearn.log_model(
            sk_model=model,
            artifact_path="model",
            registered_model_name=registered_model,
        )
        client = mlflow_module.MlflowClient()
        versions = [
            int(item.version)
            for item in client.search_model_versions(f"name='{registered_model}'")
        ]
        if not versions:
            raise RuntimeError("MLflow did not return a registered model version")
        version = max(versions)
        info = {
            "experiment": experiment,
            "run_id": run_id,
            "registered_model": registered_model,
            "version": str(version),
        }
        metadata["mlflow"] = info
        metadata["model_version"] = (
            f"late-risk-{metadata['approach']}.v{version}.{metadata['model_sha256'][:8]}"
        )
        write_release_metadata(out_dir, metadata)
        mlflow_module.log_artifacts(str(out_dir), artifact_path="bundle")

    return info


def run_train(
    connection,
    *,
    train_end: datetime,
    validation_end: datetime,
    as_of: datetime,
    out_dir: Path | str,
    mlflow_module=None,
) -> dict:
    """Train, compare with the incumbent when present, and log the validated bundle."""
    out_dir = Path(out_dir)
    source_commit = current_source_commit()
    incumbent = _load_incumbent(out_dir)
    candidate = train_candidate(
        connection,
        train_end=train_end,
        validation_end=validation_end,
        as_of=as_of,
    )
    chosen = choose_approach(candidate)
    baseline = candidate.results["baseline"]
    if incumbent is None:
        champion = baseline
        champion_source = "baseline"
    else:
        champion = ApproachResult(
            approach=f"incumbent:{incumbent.metadata['model_version']}",
            model=incumbent.model,
            metrics=_incumbent_metrics(connection, incumbent, candidate, as_of=as_of),
        )
        champion_source = "incumbent"
    gate = compare_candidate(chosen, champion, candidate.validation_order_ids)
    metadata = write_release_bundle(
        out_dir,
        chosen.model,
        model_name=f"late-risk-{chosen.approach}",
        approach=chosen.approach,
        source_commit=source_commit,
        cutoffs=_cutoffs(train_end, validation_end, as_of),
        validation=_validation_payload(candidate, chosen, champion, champion_source),
        gate=gate,
        mlflow=None,
        data={
            "schema_version": "1.0",
            "feed_as_of": as_of.isoformat(),
            "last_batch_id": None,
        },
    )
    _log_mlflow(out_dir, metadata, chosen.model, mlflow_module=mlflow_module)
    return metadata


def _database_url() -> str:
    url = os.environ.get("PROMISEOPS_DATABASE_URL")
    if not url:
        raise ValueError("PROMISEOPS_DATABASE_URL is not configured")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _release_dir() -> Path:
    artifacts = Path(os.environ.get("PROMISEOPS_ARTIFACTS", "/tmp/artifacts"))
    release_file = Path(
        os.environ.get("PROMISEOPS_RELEASE", str(artifacts / "release.json"))
    )
    return release_file.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="promiseops.worker")
    parser.add_argument("--mode", choices=["train", "monitor-train"], default="train")
    for name in ("train-end", "validation-end", "as-of"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)

    if args.mode != "train":
        print(
            "monitor-train is not implemented yet (Issue #13); refusing to report success",
            file=sys.stderr,
        )
        return 2

    train_end = instant(args.train_end)
    validation_end = instant(args.validation_end)
    as_of = instant(args.as_of)
    engine = create_engine(_database_url())
    try:
        with engine.connect() as connection:
            metadata = run_train(
                connection,
                train_end=train_end,
                validation_end=validation_end,
                as_of=as_of,
                out_dir=_release_dir(),
            )
    finally:
        engine.dispose()

    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
