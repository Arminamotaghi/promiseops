"""promiseops.training_client: request checks and the POST /training call, with a fake gateway."""

from __future__ import annotations

import json

import httpx
import pytest

from promiseops import training_client as tc

IMAGE = "registry.example/qbc13/g07/promiseops/worker@sha256:" + "a" * 64
COMMIT = "b" * 40
KEY = "group-key-123"


def request(**overrides):
    values = dict(
        image=IMAGE,
        source_commit=COMMIT,
        train_end="2017-12-31T23:59:59Z",
        validation_end="2018-03-31T23:59:59Z",
        as_of="2018-05-01T00:00:00Z",
        request_id=tc.training_request_id("promiseops_training", "manual__1"),
    )
    values.update(overrides)
    return tc.build_request(**values)


def gateway(status=202, body=None, seen=None):
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        return httpx.Response(status, json=body if body is not None else {"job": "train-x", "namespace": "g07-jobs"})

    return httpx.MockTransport(handler)


def test_request_id_is_stable_per_run_and_valid():
    first = tc.training_request_id("promiseops_training", "manual__2026-11-18")
    assert first == tc.training_request_id("promiseops_training", "manual__2026-11-18")
    assert first != tc.training_request_id("promiseops_training", "manual__2026-11-19")
    assert len(first) <= 128 and first.replace("-", "").isalnum()


def test_valid_request_is_normalised_to_utc():
    body = request(as_of="2018-05-01T02:00:00+02:00")
    assert body["as_of"] == "2018-05-01T00:00:00Z"
    assert body["mode"] == "train" and body["image"] == IMAGE


@pytest.mark.parametrize(
    "overrides",
    [
        {"image": "registry.example/qbc13/g07/promiseops/worker:latest"},
        {"image": "registry.example/worker@sha256:" + "a" * 63},
        {"source_commit": "B" * 40},
        {"source_commit": "b" * 39},
        {"mode": "deploy"},
        {"request_id": "has space"},
        {"train_end": "2018-04-01T00:00:00Z"},
        {"validation_end": "2018-06-01T00:00:00Z"},
        {"as_of": "2018-05-01T00:00:00"},
        {"as_of": "not a date"},
    ],
)
def test_invalid_request_is_rejected_before_sending(overrides):
    with pytest.raises(tc.TrainingRequestError):
        request(**overrides)


def test_submit_posts_to_training_with_bearer_key():
    seen = []
    answer = tc.submit_training("http://gw:8080/", KEY, request(), transport=gateway(seen=seen))
    assert answer == {"job": "train-x", "namespace": "g07-jobs"}
    (sent,) = seen
    assert sent.method == "POST" and str(sent.url) == "http://gw:8080/training"
    assert sent.headers["Authorization"] == f"Bearer {KEY}"
    assert json.loads(sent.content)["source_commit"] == COMMIT


@pytest.mark.parametrize("status", [200, 400, 401, 409, 500])
def test_anything_but_202_is_an_error_without_the_key(status):
    with pytest.raises(tc.TrainingSubmitError) as error:
        tc.submit_training("http://gw", KEY, request(), transport=gateway(status, {"detail": f"bad {KEY}"}))
    assert str(status) in str(error.value) and KEY not in str(error.value)


def test_unreachable_gateway_reports_the_error_type_only():
    def broken(req):
        raise httpx.ConnectError("connection refused to http://gw", request=req)

    with pytest.raises(tc.TrainingSubmitError, match="ConnectError"):
        tc.submit_training("http://gw", KEY, request(), transport=httpx.MockTransport(broken))


def test_submit_from_env(monkeypatch):
    monkeypatch.delenv("COMPANY_FEED_KEY", raising=False)
    monkeypatch.setenv("COMPANY_GATEWAY_URL", "http://gw")
    with pytest.raises(tc.TrainingSubmitError, match="COMPANY_FEED_KEY"):
        tc.submit_from_env(request(), transport=gateway())
    monkeypatch.setenv("COMPANY_FEED_KEY", KEY)
    assert tc.submit_from_env(request(), transport=gateway())["namespace"] == "g07-jobs"
