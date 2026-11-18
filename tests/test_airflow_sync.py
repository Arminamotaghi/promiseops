"""ci/airflow_sync.py: the patches for the Airflow ConfigMaps, without a cluster."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
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


def test_package_is_shipped_as_flat_files_and_imports_from_a_directory(tree, tmp_path):
    files = sync.package_files(tree / "src")
    assert sorted(files) == ["__init__.py", "mod.py"]
    # The platform mounts team-code as <dir>/promiseops with <dir> on sys.path.
    mount = tmp_path / "team-code" / "promiseops"
    mount.mkdir(parents=True)
    for name, text in files.items():
        (mount / name).write_text(text)
    code = "import sys; sys.path.insert(0, sys.argv[1]); import promiseops.mod as m; print(m.VALUE)"
    out = subprocess.run([sys.executable, "-I", "-c", code, str(mount.parent)], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "42"


def test_subpackage_stops_the_sync(tree):
    (tree / "src" / "promiseops" / "sub").mkdir()
    (tree / "src" / "promiseops" / "sub" / "x.py").write_text("")
    with pytest.raises(sync.SyncError):
        sync.package_files(tree / "src")


def test_code_patch_keeps_readme_removes_old_zip_and_deleted_modules(tree):
    files = sync.package_files(tree / "src")
    existing = {"data": {"README": "staff", "gone.py": "x"}, "binaryData": {sync.PACKAGE_KEY: "eA=="}}
    patch = sync.code_patch(files, existing, "abc")
    assert "README" not in patch["data"]
    assert patch["data"]["gone.py"] is None
    assert patch["data"]["mod.py"] == "VALUE = 42\n"
    assert patch["binaryData"] == {sync.PACKAGE_KEY: None}
    assert "binaryData" not in sync.code_patch(files, None, "abc")


def test_size_limit_stops_before_patching(tree, monkeypatch):
    monkeypatch.setattr(sync, "SIZE_LIMIT", 10)
    with pytest.raises(sync.SyncError):
        sync.dags_patch(sync.dag_files(tree / "dags"), {}, "abc")
    with pytest.raises(sync.SyncError):
        sync.code_patch(sync.package_files(tree / "src"), None, "abc")


def test_invalid_key_is_rejected(tree):
    (tree / "dags" / "bad name.py").write_text("")
    with pytest.raises(sync.SyncError):
        sync.dag_files(tree / "dags")


def test_dry_run_on_this_repository(capsys):
    assert sync.main(["--namespace", "x", "--dry-run", "--dags", str(ROOT / "dags"), "--src", str(ROOT / "src")]) == 0
    assert "nothing was changed" in capsys.readouterr().out
