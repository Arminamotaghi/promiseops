"""Pre-flight check of the group's Airflow worker.

Run it by hand after every ``airflow-sync`` and before the first ingest or training run; its
log goes into ``reports/evidence.md``. Read-only: nothing is written anywhere.

* ``check_env``: which settings the worker has. Prints PRESENT or MISSING, never a value.
* ``check_gateway``: ``GET /feed`` on the company gateway with the group key; HTTP status,
  number of batches, the manifest ``as_of``, the field names of an entry and the first and
  last ``available_at``.
* ``check_database``: connects to the team database; number of tables in ``public``.
* ``check_mlflow``: ``GET /health`` on the MLflow tracking server.
* ``check_team_code``: what ConfigMap ``team-code`` put in ``/opt/airflow/team-code`` and
  whether the ``promiseops`` modules the DAGs use import here (a missing dependency is
  reported by name).

Each check is its own task, so the grid shows exactly which one fails. Errors are reported by
type only, so no address or key reaches the log.
"""

from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime

from airflow.sdk import dag, task

REQUIRED_ENV = ("COMPANY_GATEWAY_URL", "COMPANY_FEED_KEY", "PROMISEOPS_DATABASE_URL", "MLFLOW_TRACKING_URI")
TEAM_CODE_DIR = "/opt/airflow/team-code"
TEAM_CODE_ZIP = os.path.join(TEAM_CODE_DIR, "promiseops.zip")
# Modules the ingest and training DAGs import inside their tasks.
DAG_MODULES = ("promiseops.contracts", "promiseops.gateway", "promiseops.feed_sync", "promiseops.data", "promiseops.training_client")
TIMEOUT = 15


def _setting(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set on this worker")
    return value


def _libpq_url(url: str) -> str:
    """psycopg wants a plain libpq URL; accept the SQLAlchemy spelling as well (as migrate.py)."""
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


@dag(
    dag_id="promiseops_env_check",
    schedule=None,
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 0},
    tags=["promiseops", "platform"],
    doc_md=__doc__,
)
def promiseops_env_check():
    @task
    def check_env() -> None:
        missing = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
        for name in REQUIRED_ENV:
            print(f"{name}: {'MISSING' if name in missing else 'PRESENT'}")
        print("python", sys.version.split()[0])
        if missing:
            raise RuntimeError(f"missing on this worker: {', '.join(missing)}")

    @task
    def check_gateway() -> None:
        import requests

        url = _setting("COMPANY_GATEWAY_URL").rstrip("/") + "/feed"
        headers = {"Authorization": f"Bearer {_setting('COMPANY_FEED_KEY')}"}
        try:
            response = requests.get(url, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise RuntimeError(f"gateway not reachable: {type(exc).__name__}") from None
        print("gateway GET /feed -> HTTP", response.status_code)
        if response.status_code != 200:
            raise RuntimeError(f"gateway answered HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:  # requests' JSONDecodeError is a ValueError
            raise RuntimeError("gateway answered 200 but the manifest is not JSON") from None
        entries = None
        if isinstance(payload, dict):
            entries = next((payload[k] for k in ("entries", "batches", "files") if isinstance(payload.get(k), list)), None)
            print("manifest keys:", sorted(payload))
        if entries is None:
            print("batch list not found in the manifest")
            return
        times = sorted(str(entry.get("available_at")) for entry in entries if isinstance(entry, dict))
        print("batches:", len(entries), "| as_of:", payload.get("as_of"))
        if entries and isinstance(entries[0], dict):
            # Field names only: the ingest code must use the gateway's real names.
            print("entry fields:", sorted(entries[0]))
        if times:
            print("available_at first:", times[0], "| last:", times[-1])

    @task
    def check_database() -> None:
        import psycopg

        try:
            with psycopg.connect(_libpq_url(_setting("PROMISEOPS_DATABASE_URL")), connect_timeout=TIMEOUT) as conn:
                conn.execute("SELECT 1").fetchone()
                tables = [row[0] for row in conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name")]
        except psycopg.Error as exc:
            raise RuntimeError(f"database not reachable: {type(exc).__name__}") from None
        print("database: connected")
        print(f"public tables ({len(tables)}):", ", ".join(tables) or "none")

    @task
    def check_mlflow() -> None:
        import requests

        try:
            response = requests.get(_setting("MLFLOW_TRACKING_URI").rstrip("/") + "/health", timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise RuntimeError(f"MLflow not reachable: {type(exc).__name__}") from None
        print("MLflow GET /health -> HTTP", response.status_code)
        if response.status_code != 200:
            raise RuntimeError(f"MLflow answered HTTP {response.status_code}")

    @task
    def check_team_code() -> None:
        print(f"{TEAM_CODE_DIR} on sys.path:", TEAM_CODE_DIR in sys.path)
        try:
            names = sorted(name for name in os.listdir(TEAM_CODE_DIR) if not name.startswith(".."))
        except OSError as exc:
            raise RuntimeError(f"{TEAM_CODE_DIR} not readable: {type(exc).__name__}") from None
        print(f"{TEAM_CODE_DIR}:", ", ".join(names) or "empty")
        if not os.path.isfile(TEAM_CODE_ZIP):
            raise RuntimeError(f"{TEAM_CODE_ZIP} not found; run the airflow-sync job")
        if TEAM_CODE_ZIP not in sys.path:
            sys.path.insert(0, TEAM_CODE_ZIP)
        failed = []
        for module in ("promiseops", *DAG_MODULES):
            try:
                loaded = importlib.import_module(module)
            except ModuleNotFoundError as exc:
                if exc.name == module and module != "promiseops":
                    print(f"{module}: not in the package yet")
                    continue
                print(f"{module}: FAILED, needs {exc.name}")
                failed.append(module)
            except Exception as exc:  # noqa: BLE001 - report every import problem, by type only
                print(f"{module}: FAILED, {type(exc).__name__}")
                failed.append(module)
            else:
                print(f"{module}: ok ({os.path.basename(getattr(loaded, '__file__', '') or '')})")
        if failed:
            raise RuntimeError(f"cannot import on this worker: {', '.join(failed)}")

    # Independent on purpose: one missing setting must not hide the result of the other checks.
    check_env()
    check_gateway()
    check_database()
    check_mlflow()
    check_team_code()


promiseops_env_check()
