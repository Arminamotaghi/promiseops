"""Queued training (or monitor-then-train) through the company gateway.

Airflow only orchestrates: the gateway starts OUR worker image as a queued Job in the group's
jobs namespace; fitting, evaluation and MLflow logging happen there (mlflow is not installed in
Airflow and must not be). This DAG never creates a Job or Pod itself.

* Triggered by hand with the worker image digest and its commit (both from the ``build-worker``
  job of the main pipeline) and the cutoffs. Defaults are the split in
  docs/design/features-and-split.md: train_end 2017-12-31, validation_end 2018-03-31,
  as_of 2018-05-01. The data must be ingested up to ``as_of`` first (promiseops_ingest).
* ``mode=train`` always fits and evaluates; ``mode=monitor-train`` first stores a monitoring
  report and trains only if it recommends retraining. A later ``as_of`` replays a later period.
  Neither mode promotes or deploys a model: the release pin is a separate, reviewed step.
* retries: 2. ``request_id`` comes from the DagRun, so a retry resends the same request and the
  gateway does not start a second Job for the same run.
* A 202 answer means "submitted for admission", not "trained": follow the Job in the jobs
  namespace (CI job inspect-own-namespaces) and the run in MLflow.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

from airflow.sdk import Param, dag, get_current_context, task

TEAM_CODE_ZIP = "/opt/airflow/team-code/promiseops.zip"
if os.path.exists(TEAM_CODE_ZIP) and TEAM_CODE_ZIP not in sys.path:
    sys.path.insert(0, TEAM_CODE_ZIP)


@dag(
    dag_id="promiseops_training",
    schedule=None,
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=1)},
    params={
        "worker_image": Param("", type="string", description="<registry>/<group path>/worker@sha256:<64 hex>"),
        "source_commit": Param("", type="string", description="40-character commit the worker image was built from"),
        "mode": Param("train", enum=["train", "monitor-train"]),
        "train_end": Param("2017-12-31T23:59:59Z", type="string"),
        "validation_end": Param("2018-03-31T23:59:59Z", type="string"),
        "as_of": Param("2018-05-01T00:00:00Z", type="string"),
    },
    tags=["promiseops", "model"],
    doc_md=__doc__,
)
def promiseops_training():
    @task
    def submit() -> dict:
        from promiseops.training_client import build_request, submit_from_env, training_request_id

        context = get_current_context()
        params = context["params"]
        request = build_request(
            image=params["worker_image"],
            source_commit=params["source_commit"],
            mode=params["mode"],
            train_end=params["train_end"],
            validation_end=params["validation_end"],
            as_of=params["as_of"],
            request_id=training_request_id(context["dag"].dag_id, context["dag_run"].run_id),
        )
        answer = submit_from_env(request)
        print(
            f"submitted request_id={request['request_id']} mode={request['mode']} "
            f"cutoffs={request['train_end']}/{request['validation_end']}/{request['as_of']} gateway={answer}"
        )
        return {"request": request, "response": answer}

    submit()


promiseops_training()
