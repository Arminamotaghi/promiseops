import os
import sys
from datetime import datetime
from airflow import DAG
from airflow.operators.python import PythonOperator

TEAM_CODE_ZIP = "/opt/airflow/team-code/promiseops.zip"
if os.path.exists(TEAM_CODE_ZIP) and TEAM_CODE_ZIP not in sys.path:
    sys.path.insert(0, TEAM_CODE_ZIP)


def run_feed_sync(**context):
    from promiseops.feed_sync import main

    conf = context.get("dag_run").conf or {}
    as_of = conf.get("as_of")

    argv = []
    if as_of:
        argv.extend(["--as-of", str(as_of)])

    exit_code = main(argv)
    if exit_code != 0:
        raise RuntimeError(f"promiseops.feed_sync.main failed with exit code {exit_code}")


with DAG(
    dag_id="promiseops_ingest",
    schedule=None,
    max_active_runs=1,
    start_date=datetime(2026, 1, 1),
    catchup=False,
) as dag:
    sync_task = PythonOperator(
        task_id="sync_feed_task",
        python_callable=run_feed_sync,
        op_kwargs={},
    )
