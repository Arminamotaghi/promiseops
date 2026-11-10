from fastapi.testclient import TestClient

from promiseops.api import create_app


def test_predict_requires_api_key(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")
    client = TestClient(create_app())

    response = client.post(
        "/predict",
        json={"order_id": "order-123"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_predict_returns_503_when_model_is_unavailable(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")
    client = TestClient(create_app())

    response = client.post(
        "/predict",
        headers={"X-API-Key": "test-secret"},
        json={"order_id": "order-123"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "model_unavailable"


def test_predict_rejects_invalid_input(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")
    client = TestClient(create_app())
    headers = {"X-API-Key": "test-secret"}

    invalid_id = client.post(
        "/predict",
        headers=headers,
        json={"order_id": "../../secret"},
    )
    assert invalid_id.status_code == 422

    extra_field = client.post(
        "/predict",
        headers=headers,
        json={"order_id": "order-123", "extra": "value"},
    )
    assert extra_field.status_code == 422


def test_predict_rejects_wrong_api_key(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")
    client = TestClient(create_app())

    response = client.post(
        "/predict",
        headers={"X-API-Key": "wrong-secret"},
        json={"order_id": "order-123"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
