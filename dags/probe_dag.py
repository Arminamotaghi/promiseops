from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime
import sys
import os
import importlib

def probe_environment():
    print("=" * 40)
    # 1. Print Python version
    print(f"Python Version: {sys.version}")
    print("=" * 40)

    # 2. Test imports for target libraries
    libraries = ["promiseops", "sqlalchemy", "psycopg", "pydantic", "httpx"]
    print("Library Import Test:")
    for lib in libraries:
        try:
            importlib.import_module(lib)
            print(f"✅ {lib}: Import Successful")
        except Exception as e:
            print(f"❌ {lib}: Import Failed ({type(e).__name__}: {e})")
    print("=" * 40)

    # 3. Print ONLY variable names starting with PROMISEOPS (no values for security)
    print("Environment Variables (PROMISEOPS_*):")
    env_vars = [v for v in os.environ.keys() if v.startswith("PROMISEOPS")]
    if env_vars:
        for var in sorted(env_vars):
            print(f"Found: {var}")
    else:
        print("No PROMISEOPS environment variables found.")
    print("=" * 40)
    print("Probe Complete.")

with DAG(
    dag_id='probe_environment_g07',
    start_date=datetime(2023, 1, 1),
    schedule=None,  # Manual trigger only
    catchup=False,
    tags=['probe', 'team_g07'],
) as dag:

    probe_task = PythonOperator(
        task_id='run_environment_probe',
        python_callable=probe_environment,
    )
