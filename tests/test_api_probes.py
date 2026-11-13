from fastapi.testclient import TestClient

from promiseops.api import create_app


def test_healthz_returns_200():
    client = TestClient(create_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_503_when_not_ready():
    client = TestClient(create_app())
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_readyz_returns_200_when_model_and_database_are_ready():
    client = TestClient(
        create_app(model=object(), database_ready=lambda: True)
    )
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readyz_returns_503_when_database_is_not_ready():
    client = TestClient(
        create_app(model=object(), database_ready=lambda: False)
    )
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_metrics_returns_prometheus_output(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")
    client = TestClient(create_app())
    response = client.get(
        "/metrics",
        headers={"X-API-Key": "test-secret"},
    )
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "promiseops_http_requests_total" in response.text


def test_metrics_expose_http_counter_histogram_and_model_gauge(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")
    client = TestClient(create_app())

    client.get("/healthz")
    response = client.get(
        "/metrics",
        headers={"X-API-Key": "test-secret"},
    )

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "promiseops_http_requests_total" in response.text
    assert 'route="/healthz"' in response.text
    assert 'method="GET"' in response.text
    assert "promiseops_http_request_duration_seconds" in response.text
    assert "promiseops_model_ready" in response.text
    assert "promiseops_readiness" in response.text
    assert "promiseops_model_info" in response.text
