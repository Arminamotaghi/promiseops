"""Queued worker entry point invoked by the staff's bounded Job dispatcher."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine

from promiseops.contracts import instant
from promiseops.model import (
    ApproachResult,
    Candidate,
    compare_candidate,
    train_candidate,
)
from promiseops.release import current_source_commit, write_release_bundle


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


def _validation_payload(candidate: Candidate, chosen: ApproachResult) -> dict:
    return {
        "cohort_hash": chosen.metrics.cohort_hash,
        "n": chosen.metrics.n,
        "prevalence": chosen.metrics.prevalence,
        "k": chosen.metrics.k,
        "recall_at_10": chosen.metrics.recall_at_10,
        "precision_at_10": chosen.metrics.precision_at_10,
        "average_precision": chosen.metrics.average_precision,
        "brier": chosen.metrics.brier,
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


def run_train(
    connection,
    *,
    train_end: datetime,
    validation_end: datetime,
    as_of: datetime,
    out_dir: Path | str,
) -> dict:
    """Train, select the best approach, write a bundle and validate it."""
    candidate = train_candidate(
        connection,
        train_end=train_end,
        validation_end=validation_end,
        as_of=as_of,
    )
    chosen = choose_approach(candidate)
    baseline = candidate.results["baseline"]
    gate = compare_candidate(chosen, baseline, candidate.validation_order_ids)
    return write_release_bundle(
        out_dir,
        chosen.model,
        model_name=f"late-risk-{chosen.approach}",
        approach=chosen.approach,
        source_commit=current_source_commit(),
        cutoffs=_cutoffs(train_end, validation_end, as_of),
        validation=_validation_payload(candidate, chosen),
        gate=gate,
        mlflow=None,
        data={
            "schema_version": "1.0",
            "feed_as_of": as_of.isoformat(),
            "last_batch_id": None,
        },
    )


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
        raise NotImplementedError("monitor-train is not implemented yet")

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
