"""Pin a reviewed, trained model bundle into the service image.

The human approval is a merge request that changes only ``release/pin.json``::

    {"model_version": "late-risk-hist_gradient_boosting.v3.4f2a91c0",
     "mlflow_run_id": "<32 hex characters>",
     "model_sha256": "<64 hex characters>"}

``python -m promiseops.pin fetch`` (CI job ``fetch-release``) downloads ``bundle/`` from that
MLflow run and checks it. ``python -m promiseops.pin check`` (image build) checks it again
with the image's own Python and scikit-learn before it is copied to
``/opt/promiseops/release``. A bundle is accepted only if it matches the pin exactly, names
the same MLflow run and passed the gate; a bootstrap or rejected candidate never ships.
Design: ``docs/design/release.md``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from promiseops.release import BundleError, ReleaseBundle, load_bundle

PIN_KEYS = ("model_version", "mlflow_run_id", "model_sha256")
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PinError(Exception):
    """The pin file or the bundle it names cannot be released."""


def read_pin(path: Path | str) -> dict:
    try:
        pin = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PinError(f"cannot read {path}") from exc
    if not isinstance(pin, dict) or set(pin) != set(PIN_KEYS):
        raise PinError(f"pin must have exactly these keys: {', '.join(PIN_KEYS)}")
    if not isinstance(pin["model_version"], str) or not pin["model_version"]:
        raise PinError("model_version must be a non-empty string")
    if not isinstance(pin["mlflow_run_id"], str) or not _RUN_ID.match(pin["mlflow_run_id"]):
        raise PinError("mlflow_run_id must be 32 lowercase hex characters")
    if not isinstance(pin["model_sha256"], str) or not _SHA256.match(pin["model_sha256"]):
        raise PinError("model_sha256 must be 64 lowercase hex characters")
    return pin


def check_bundle(bundle_dir: Path | str, pin: dict) -> ReleaseBundle:
    """Load the bundle with the startup checks, then compare it with the pin."""
    try:
        bundle = load_bundle(bundle_dir)
    except BundleError as exc:
        raise PinError(f"bundle failed the startup checks: {exc}") from exc
    metadata = bundle.metadata
    if metadata.get("model_version") != pin["model_version"]:
        raise PinError("bundle model_version does not match the pin")
    if metadata.get("model_sha256") != pin["model_sha256"]:
        raise PinError("bundle model_sha256 does not match the pin")
    mlflow = metadata.get("mlflow")
    if not isinstance(mlflow, dict) or mlflow.get("run_id") != pin["mlflow_run_id"]:
        raise PinError("bundle was not recorded by the pinned MLflow run")
    gate = metadata.get("gate")
    decision = gate.get("decision") if isinstance(gate, dict) else None
    if decision != "accepted":
        raise PinError(f"gate decision is {decision!r}; only an accepted candidate can be released")
    return bundle


def fetch(pin_path: Path | str, out_dir: Path | str, *, mlflow_module=None) -> ReleaseBundle:
    """Download ``bundle/`` from the pinned run into ``out_dir`` and check it."""
    pin = read_pin(pin_path)
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise PinError(f"{out} is not empty")
    if mlflow_module is None:
        import mlflow as mlflow_module  # installed with requirements-tracking.lock only
    with tempfile.TemporaryDirectory() as tmp:
        try:
            downloaded = mlflow_module.artifacts.download_artifacts(
                run_id=pin["mlflow_run_id"], artifact_path="bundle", dst_path=tmp
            )
        except Exception as exc:  # MLflow raises several types; the message is enough here
            raise PinError(f"cannot download bundle/ from run {pin['mlflow_run_id']}: {exc}") from exc
        out.mkdir(parents=True, exist_ok=True)
        shutil.copytree(downloaded, out, dirs_exist_ok=True)
    return check_bundle(out, pin)


def _summary(bundle: ReleaseBundle) -> dict:
    metadata = bundle.metadata
    return {
        "model_version": metadata["model_version"],
        "model_sha256": metadata["model_sha256"],
        "mlflow_run_id": metadata["mlflow"]["run_id"],
        "gate": metadata["gate"]["decision"],
        "bundle": str(bundle.path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m promiseops.pin", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "check"):
        command = commands.add_parser(name)
        command.add_argument("--pin", default="release/pin.json")
        command.add_argument("--bundle", default="release/bundle")
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch":
            bundle = fetch(args.pin, args.bundle)
        else:
            bundle = check_bundle(args.bundle, read_pin(args.pin))
    except PinError as exc:
        print(f"pin {args.command} failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(_summary(bundle), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
