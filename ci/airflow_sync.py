"""Publish the Airflow DAGs and the promiseops package to the team ConfigMaps.

The group's Airflow has no git-sync. It reads DAG files from the ConfigMap ``team-dags`` and
puts ``/opt/airflow/team-code`` (the ConfigMap ``team-code``) on ``sys.path``; both live in
the ``<team>-platform`` namespace. CI may update/patch these two ConfigMaps, not create them.

* ``team-dags``: one key per ``dags/*.py``. A ``.py`` key whose file is no longer in ``dags/``
  is removed (only when the current keys can be read). Every other key, such as the staff
  ``README``, is never touched.
* ``team-code``: one key per file of ``src/promiseops`` (``__init__.py``, ``data.py``, ...).
  The platform mounts this ConfigMap as the directory ``/opt/airflow/team-code/promiseops``
  and ``/opt/airflow/team-code`` is on ``sys.path``, so ``import promiseops`` works in every
  task with no path changes (seen in the promiseops_env_check log of 27 Aban). ConfigMap keys
  cannot contain ``/``, so the package must stay flat; a subpackage stops the sync. A ``.py``
  key whose file left the package, and the ``promiseops.zip`` key of the first version, are
  removed. Every other key (the staff ``README``) is left alone.

Both are JSON merge patches: keys that are not named are left as they are. Each ConfigMap
must stay under the platform limit (about 900 KB); the script stops before patching if not.
It prints key names and sizes only.

Usage (CI job ``airflow-sync``, manual, main only)::

    python ci/airflow_sync.py --namespace g07-platform --commit "$CI_COMMIT_SHA"
    python ci/airflow_sync.py --namespace g07-platform --dry-run   # build and check only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DAGS_CONFIGMAP = "team-dags"
CODE_CONFIGMAP = "team-code"
PACKAGE_KEY = "promiseops.zip"  # first version only; removed on the next sync
SIZE_LIMIT = 900_000  # bytes per ConfigMap, as given by the course staff
COMMIT_ANNOTATION = "promiseops/source-commit"
DIGEST_ANNOTATION = "promiseops/content-sha256"

_KEY = re.compile(r"^[-._a-zA-Z0-9]{1,253}$")


class SyncError(RuntimeError):
    pass


def dag_files(dags_dir: Path) -> dict[str, str]:
    files = {}
    for path in sorted(dags_dir.glob("*.py")):
        if not _KEY.fullmatch(path.name):
            raise SyncError(f"{path.name}: not a valid ConfigMap key")
        files[path.name] = path.read_text(encoding="utf-8")
    if not files:
        raise SyncError(f"no .py files in {dags_dir}")
    return files


def package_files(src_dir: Path, package: str = "promiseops") -> dict[str, str]:
    root = src_dir / package
    if not (root / "__init__.py").is_file():
        raise SyncError(f"{root} is not a Python package")
    files = {}
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo") or path.is_dir():
            continue
        if path.parent != root:
            raise SyncError(f"{path.relative_to(src_dir)}: subpackages cannot be shipped through a ConfigMap")
        if not _KEY.fullmatch(path.name):
            raise SyncError(f"{path.name}: not a valid ConfigMap key")
        files[path.name] = path.read_text(encoding="utf-8")
    return files


def digest(data: dict[str, str], binary: dict[str, str]) -> str:
    canonical = json.dumps({"data": data, "binaryData": binary}, sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def configmap_size(data: dict[str, str | None], binary: dict[str, str | None]) -> int:
    """Approximate stored size: key names plus values (binaryData counted as base64)."""
    size = sum(len(k) + len((v or "").encode()) for k, v in data.items() if v is not None)
    return size + sum(len(k) + len(v or "") for k, v in binary.items() if v is not None)


def dags_patch(files: dict[str, str], existing: dict[str, str] | None, commit: str) -> dict:
    """Merge patch for team-dags. ``existing`` is the current ``data`` (None if unreadable)."""
    data: dict[str, str | None] = dict(files)
    if existing is not None:
        for key in existing:
            if key.endswith(".py") and key not in files:
                data[key] = None  # a DAG file deleted from the repository
    kept = {k: v for k, v in (existing or {}).items() if k not in data}
    if configmap_size({**kept, **data}, {}) > SIZE_LIMIT:
        raise SyncError(f"{DAGS_CONFIGMAP} would exceed {SIZE_LIMIT} bytes")
    annotations = {COMMIT_ANNOTATION: commit, DIGEST_ANNOTATION: digest(files, {})}
    return {"metadata": {"annotations": annotations}, "data": data}


def code_patch(files: dict[str, str], existing: dict | None, commit: str) -> dict:
    """Merge patch for team-code. ``existing`` is the current ConfigMap (None if unreadable)."""
    data: dict[str, str | None] = dict(files)
    binary: dict[str, None] = {}
    if existing is not None:
        for key in existing.get("data") or {}:
            if key.endswith(".py") and key not in files:
                data[key] = None  # a module removed from the package
        if PACKAGE_KEY in (existing.get("binaryData") or {}):
            binary[PACKAGE_KEY] = None  # the zip shipped by the first version of this job
    kept = {k: v for k, v in ((existing or {}).get("data") or {}).items() if k not in data}
    if configmap_size({**kept, **data}, {}) > SIZE_LIMIT:
        raise SyncError(f"{CODE_CONFIGMAP} would exceed {SIZE_LIMIT} bytes")
    patch = {"metadata": {"annotations": {COMMIT_ANNOTATION: commit, DIGEST_ANNOTATION: digest(files, {})}}, "data": data}
    if binary:
        patch["binaryData"] = binary
    return patch


def kubectl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, check=False)


def read_configmap(namespace: str, name: str) -> dict | None:
    result = kubectl("-n", namespace, "get", "configmap", name, "-o", "json")
    if result.returncode != 0:
        print(f"note: cannot read {namespace}/{name} ({result.stderr.strip()[:200]}); stale keys are not removed")
        return None
    return json.loads(result.stdout)


def apply_patch(namespace: str, name: str, patch: dict) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(patch, handle)
    result = kubectl("-n", namespace, "patch", "configmap", name, "--type", "merge", "--patch-file", handle.name)
    Path(handle.name).unlink(missing_ok=True)
    if result.returncode != 0:
        raise SyncError(f"patch {namespace}/{name} failed: {result.stderr.strip()[:500]}")
    print(result.stdout.strip())


def report(name: str, patch: dict) -> None:
    print(f"{name}:")
    for section in ("data", "binaryData"):
        for key, value in sorted((patch.get(section) or {}).items()):
            print(f"  {key}: removed" if value is None else f"  {key}: {len(value.encode())} bytes ({section})")
    print(f"  {DIGEST_ANNOTATION}: {patch['metadata']['annotations'][DIGEST_ANNOTATION]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--commit", default="unknown")
    parser.add_argument("--dags", type=Path, default=Path("dags"))
    parser.add_argument("--src", type=Path, default=Path("src"))
    parser.add_argument("--dry-run", action="store_true", help="build and check the patches without kubectl")
    args = parser.parse_args(argv)
    try:
        files, package = dag_files(args.dags), package_files(args.src)
        current_dags = None if args.dry_run else read_configmap(args.namespace, DAGS_CONFIGMAP)
        current_code = None if args.dry_run else read_configmap(args.namespace, CODE_CONFIGMAP)
        patches = {
            DAGS_CONFIGMAP: dags_patch(
                files, (current_dags.get("data") or {}) if current_dags is not None else None, args.commit
            ),
            CODE_CONFIGMAP: code_patch(package, current_code, args.commit),
        }
        for name, patch in patches.items():
            report(name, patch)
            if not args.dry_run:
                apply_patch(args.namespace, name, patch)
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("dry run: nothing was changed" if args.dry_run else "synced; the DAGs (or import errors) appear in the Airflow UI within a few minutes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
