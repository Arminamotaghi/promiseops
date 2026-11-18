"""ci/airflow_sync.py: the patches for the Airflow ConfigMaps, without a cluster."""

from __future__ import annotations

import base64
import importlib.util
import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("airflow_sync", ROOT / "ci" / "airflow_sync.py")
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "dags").mkdir()
    (tmp_path / "dags" / "a_dag.py").write_text("print('a')\n")
    (tmp_path / "dags" / "b_dag.py").write_text("print('b')\n")
    (tmp_path / "dags" / "notes.md").write_text("not a DAG\n")
    pkg = tmp_path / "src" / "promiseops"
    (pkg / "__pycache__").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text("VALUE = 42\n")
    (pkg / "__pycache__" / "mod.cpython-312.pyc").write_bytes(b"\0")
    return tmp_path


def test_only_python_files_become_keys(tree):
    assert sorted(sync.dag_files(tree / "dags")) == ["a_dag.py", "b_dag.py"]


def test_staff_readme_is_kept_and_deleted_dags_are_removed(tree):
    existing = {"README": "staff", "a_dag.py": "old", "gone_dag.py": "old"}
    patch = sync.dags_patch(sync.dag_files(tree / "dags"), existing, "abc")
    assert "README" not in patch["data"]  # merge patch: unnamed keys stay as they are
    assert patch["data"]["gone_dag.py"] is None
    assert patch["data"]["a_dag.py"] == "print('a')\n"
    assert patch["metadata"]["annotations"][sync.COMMIT_ANNOTATION] == "abc"


def test_nothing_is_removed_when_current_keys_cannot_be_read(tree):
    patch = sync.dags_patch(sync.dag_files(tree / "dags"), None, "abc")
    assert None not in patch["data"].values()


def test_package_zip_imports_and_is_reproducible(tree, tmp_path):
    archive = sync.package_zip(tree / "src")
    assert archive == sync.package_zip(tree / "src")
    names = zipfile.ZipFile(io.BytesIO(archive)).namelist()
    assert names == ["promiseops/__init__.py", "promiseops/mod.py"]
    target = tmp_path / "promiseops.zip"
    target.write_bytes(archive)
    code = "import sys; sys.path.insert(0, sys.argv[1]); import promiseops.mod as m; print(m.VALUE)"
    out = subprocess.run([sys.executable, "-I", "-c", code, str(target)], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "42"


def test_code_patch_is_binary_and_keeps_other_keys(tree):
    archive = sync.package_zip(tree / "src")
    patch = sync.code_patch(archive, {"data": {"README": "staff"}}, "abc")
    assert base64.b64decode(patch["binaryData"][sync.PACKAGE_KEY]) == archive
    assert "data" not in patch


def test_size_limit_stops_before_patching(tree, monkeypatch):
    monkeypatch.setattr(sync, "SIZE_LIMIT", 10)
    with pytest.raises(sync.SyncError):
        sync.dags_patch(sync.dag_files(tree / "dags"), {}, "abc")
    with pytest.raises(sync.SyncError):
        sync.code_patch(sync.package_zip(tree / "src"), None, "abc")


def test_invalid_key_is_rejected(tree):
    (tree / "dags" / "bad name.py").write_text("")
    with pytest.raises(sync.SyncError):
        sync.dag_files(tree / "dags")


def test_dry_run_on_this_repository(capsys):
    assert sync.main(["--namespace", "x", "--dry-run", "--dags", str(ROOT / "dags"), "--src", str(ROOT / "src")]) == 0
    assert "nothing was changed" in capsys.readouterr().out
