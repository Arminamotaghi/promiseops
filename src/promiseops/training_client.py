"""Submit queued training through the company gateway (docs/CONTRACTS.md, "Queued worker interface").

Airflow only orchestrates. ``POST /training`` asks the gateway to start our worker image as a
queued Job in the group's jobs namespace; fitting, evaluation and MLflow logging happen there,
never inside an Airflow task. A 202 answer means "submitted for admission", not "trained":
follow the Job in the jobs namespace and the run in MLflow.

Everything the gateway would reject is checked here first, so a bad request fails in the DAG
with a clear message instead of starting nothing:

* ``image``: our worker image pinned by digest (``...@sha256:<64 hex>``), never a tag;
* ``source_commit``: 40 lowercase hex characters, the commit the image was built from;
* ``train_end < validation_end <= as_of``, all timezone-aware (sent in UTC);
* ``mode``: ``train`` or ``monitor-train``;
* ``request_id``: derived from the Airflow DagRun, so a task retry resends the same ID and the
  gateway does not start a second Job for the same run.

The gateway URL and the group key come from ``COMPANY_GATEWAY_URL`` and ``COMPANY_FEED_KEY``
(set on the Airflow worker by the platform). The key is never printed.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone

import httpx

MODES = ("train", "monitor-train")
_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class TrainingRequestError(ValueError):
    """The request would be rejected by the gateway; nothing was sent."""


class TrainingSubmitError(RuntimeError):
    """The gateway could not be reached or did not accept the request."""


def training_request_id(dag_id: str, run_id: str) -> str:
    """Stable ID for one DagRun: retries of that run reuse it, another run gets another ID."""
    if not dag_id or not run_id:
        raise TrainingRequestError("dag_id and run_id are required")
    return "train-" + hashlib.sha256(f"{dag_id}\n{run_id}".encode()).hexdigest()[:40]


def _instant(name: str, value: str | datetime) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise TrainingRequestError(f"{name} is not an ISO timestamp") from None
    if parsed.tzinfo is None:
        raise TrainingRequestError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def build_request(
    *,
    image: str,
    source_commit: str,
    train_end: str | datetime,
    validation_end: str | datetime,
    as_of: str | datetime,
    request_id: str,
    mode: str = "train",
) -> dict:
    image, source_commit = image.strip(), source_commit.strip()
    if not _IMAGE.fullmatch(image):
        raise TrainingRequestError("image must be our worker image pinned by digest: <registry>/<path>/worker@sha256:<64 hex>")
    if not _COMMIT.fullmatch(source_commit):
        raise TrainingRequestError("source_commit must be 40 lowercase hex characters")
    if mode not in MODES:
        raise TrainingRequestError(f"mode must be one of {', '.join(MODES)}")
    if not _REQUEST_ID.fullmatch(request_id):
        raise TrainingRequestError("request_id must be 1-128 letters, digits, '_', '.' or '-'")
    train, validation, cutoff = (
        _instant("train_end", train_end),
        _instant("validation_end", validation_end),
        _instant("as_of", as_of),
    )
    if not train < validation <= cutoff:
        raise TrainingRequestError("cutoffs must satisfy train_end < validation_end <= as_of")
    return {
        "image": image,
        "source_commit": source_commit,
        "train_end": _utc(train),
        "validation_end": _utc(validation),
        "as_of": _utc(cutoff),
        "mode": mode,
        "request_id": request_id,
    }


def submit_training(
    base_url: str,
    feed_key: str,
    request: dict,
    *,
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """POST the request; return the gateway's answer (Job name and namespace) on 202."""
    if not base_url or not feed_key:
        raise TrainingSubmitError("gateway URL and key are required")
    headers = {"Authorization": f"Bearer {feed_key}", "Accept": "application/json"}
    try:
        with httpx.Client(headers=headers, timeout=timeout, transport=transport) as client:
            response = client.post(base_url.rstrip("/") + "/training", json=request)
    except httpx.HTTPError as exc:
        raise TrainingSubmitError(f"gateway not reachable: {type(exc).__name__}") from None
    if response.status_code != 202:
        detail = response.text[:300].replace(feed_key, "***")
        raise TrainingSubmitError(f"gateway answered HTTP {response.status_code}: {detail}")
    try:
        answer = response.json()
    except ValueError:
        raise TrainingSubmitError("gateway answered 202 without a JSON body") from None
    if not isinstance(answer, dict):
        raise TrainingSubmitError("gateway answered 202 with an unexpected body")
    return answer


def submit_from_env(request: dict, *, transport: httpx.BaseTransport | None = None) -> dict:
    """Submit with ``COMPANY_GATEWAY_URL`` and ``COMPANY_FEED_KEY`` from the environment."""
    url, key = os.environ.get("COMPANY_GATEWAY_URL", "").strip(), os.environ.get("COMPANY_FEED_KEY", "").strip()
    missing = [name for name, value in (("COMPANY_GATEWAY_URL", url), ("COMPANY_FEED_KEY", key)) if not value]
    if missing:
        raise TrainingSubmitError(f"missing on this worker: {', '.join(missing)}")
    return submit_training(url, key, request, transport=transport)
