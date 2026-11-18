import json
import shutil
from pathlib import Path

import pytest

from promiseops import pin
from promiseops.release import build_baseline_bundle

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "a" * 32


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    out = tmp_path_factory.mktemp("baseline") / "bundle"
    build_baseline_bundle(out, fixtures_dir=ROOT / "fixtures", source_commit="b" * 40)
    return out


def _trained(baseline: Path, target: Path, *, decision="accepted", run_id=RUN_ID) -> dict:
    """A copy of the baseline bundle relabelled as a trained, gated candidate."""
    shutil.copytree(baseline, target)
    path = target / "release.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["model_version"] = "late-risk-hist_gradient_boosting.v3." + metadata["model_sha256"][:8]
    metadata["gate"] = {"decision": decision}
    metadata["mlflow"] = {"run_id": run_id, "version": "3"}
    path.write_text(json.dumps(metadata), encoding="utf-8")
    return metadata


def _pin_file(directory: Path, metadata: dict, **overrides) -> Path:
    content = {
        "model_version": metadata["model_version"],
        "mlflow_run_id": RUN_ID,
        "model_sha256": metadata["model_sha256"],
        **overrides,
    }
    path = directory / "pin.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


class FakeArtifacts:
    def __init__(self, source: Path | None, error: Exception | None = None):
        self.source = source
        self.error = error
        self.calls = []

    def download_artifacts(self, *, run_id, artifact_path, dst_path):
        self.calls.append((run_id, artifact_path))
        if self.error:
            raise self.error
        target = Path(dst_path) / artifact_path
        shutil.copytree(self.source, target)
        return str(target)


class FakeMlflow:
    def __init__(self, source=None, error=None):
        self.artifacts = FakeArtifacts(source, error)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ({"model_version": "v", "mlflow_run_id": RUN_ID}, "exactly these keys"),
        ({"model_version": "", "mlflow_run_id": RUN_ID, "model_sha256": "0" * 64}, "model_version"),
        ({"model_version": "v", "mlflow_run_id": "latest", "model_sha256": "0" * 64}, "mlflow_run_id"),
        ({"model_version": "v", "mlflow_run_id": RUN_ID, "model_sha256": "ABC"}, "model_sha256"),
    ],
)
def test_read_pin_rejects_incomplete_or_malformed_pins(tmp_path, content, message):
    path = tmp_path / "pin.json"
    path.write_text(json.dumps(content), encoding="utf-8")

    with pytest.raises(pin.PinError, match=message):
        pin.read_pin(path)


def test_check_accepts_the_pinned_accepted_bundle(baseline, tmp_path):
    metadata = _trained(baseline, tmp_path / "bundle")

    bundle = pin.check_bundle(tmp_path / "bundle", pin.read_pin(_pin_file(tmp_path, metadata)))

    assert bundle.metadata["model_version"] == metadata["model_version"]


@pytest.mark.parametrize("decision", ["rejected", "bootstrap"])
def test_check_refuses_a_candidate_that_did_not_pass_the_gate(baseline, tmp_path, decision):
    metadata = _trained(baseline, tmp_path / "bundle", decision=decision)

    with pytest.raises(pin.PinError, match="only an accepted candidate"):
        pin.check_bundle(tmp_path / "bundle", pin.read_pin(_pin_file(tmp_path, metadata)))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"model_version": "late-risk-logistic_regression.v2.00000000"}, "model_version does not match"),
        ({"model_sha256": "0" * 64}, "model_sha256 does not match"),
        ({"mlflow_run_id": "c" * 32}, "pinned MLflow run"),
    ],
)
def test_check_refuses_a_bundle_that_differs_from_the_pin(baseline, tmp_path, overrides, message):
    metadata = _trained(baseline, tmp_path / "bundle")

    with pytest.raises(pin.PinError, match=message):
        pin.check_bundle(tmp_path / "bundle", pin.read_pin(_pin_file(tmp_path, metadata, **overrides)))


def test_check_refuses_a_tampered_model_file(baseline, tmp_path):
    metadata = _trained(baseline, tmp_path / "bundle")
    model_file = tmp_path / "bundle" / metadata["model_file"]
    model_file.write_bytes(model_file.read_bytes() + b"x")

    with pytest.raises(pin.PinError, match="startup checks"):
        pin.check_bundle(tmp_path / "bundle", pin.read_pin(_pin_file(tmp_path, metadata)))


def test_fetch_downloads_bundle_from_the_pinned_run(baseline, tmp_path):
    metadata = _trained(baseline, tmp_path / "source")
    fake = FakeMlflow(tmp_path / "source")

    bundle = pin.fetch(_pin_file(tmp_path, metadata), tmp_path / "out", mlflow_module=fake)

    assert fake.artifacts.calls == [(RUN_ID, "bundle")]
    assert (tmp_path / "out" / "release.json").is_file()
    assert bundle.metadata["mlflow"]["run_id"] == RUN_ID


def test_fetch_reports_download_failures(baseline, tmp_path):
    metadata = _trained(baseline, tmp_path / "source")
    fake = FakeMlflow(error=OSError("connection refused"))

    with pytest.raises(pin.PinError, match="cannot download bundle/"):
        pin.fetch(_pin_file(tmp_path, metadata), tmp_path / "out", mlflow_module=fake)


def test_fetch_never_mixes_with_an_existing_bundle(baseline, tmp_path):
    metadata = _trained(baseline, tmp_path / "source")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "release.json").write_text("{}", encoding="utf-8")

    with pytest.raises(pin.PinError, match="not empty"):
        pin.fetch(
            _pin_file(tmp_path, metadata), tmp_path / "out", mlflow_module=FakeMlflow(tmp_path / "source")
        )


def test_main_check_prints_a_summary_and_fails_cleanly(baseline, tmp_path, capsys):
    metadata = _trained(baseline, tmp_path / "bundle")
    pin_path = _pin_file(tmp_path, metadata)

    assert pin.main(["check", "--pin", str(pin_path), "--bundle", str(tmp_path / "bundle")]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["gate"] == "accepted"
    assert summary["mlflow_run_id"] == RUN_ID

    _pin_file(tmp_path, metadata, model_sha256="0" * 64)
    assert pin.main(["check", "--pin", str(pin_path), "--bundle", str(tmp_path / "bundle")]) == 1
    assert "pin check failed: bundle model_sha256" in capsys.readouterr().err
