from unittest.mock import MagicMock

from promiseops import api

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


def _mock_monitoring_engine(value):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value

    connection.execute.return_value.scalar_one_or_none.return_value = value

    return engine, connection


def _get_metrics(client):
    return client.get(
        "/metrics",
        headers={"X-API-Key": "test-secret"},
    )


def test_retraining_metric_is_zero_when_no_report_exists(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")
    api.RETRAINING_REQUIRED.set(1)

    engine, connection = _mock_monitoring_engine(None)

    monkeypatch.setattr(
        api,
        "create_database_engine",
        lambda: engine,
    )

    with TestClient(api.create_app()) as client:
        response = _get_metrics(client)

    assert response.status_code == 200
    assert "promiseops_retraining_required 0.0" in response.text

    query = str(connection.execute.call_args.args[0])
    assert "ORDER BY as_of DESC, report_id DESC" in query


def test_retraining_metric_is_zero_when_latest_report_is_false(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")
    api.RETRAINING_REQUIRED.set(1)

    engine, _ = _mock_monitoring_engine(False)

    monkeypatch.setattr(
        api,
        "create_database_engine",
        lambda: engine,
    )

    with TestClient(api.create_app()) as client:
        response = _get_metrics(client)

    assert response.status_code == 200
    assert "promiseops_retraining_required 0.0" in response.text


def test_retraining_metric_is_one_when_latest_report_is_true(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")
    api.RETRAINING_REQUIRED.set(0)

    engine, _ = _mock_monitoring_engine(True)

    monkeypatch.setattr(
        api,
        "create_database_engine",
        lambda: engine,
    )

    with TestClient(api.create_app()) as client:
        response = _get_metrics(client)

    assert response.status_code == 200
    assert "promiseops_retraining_required 1.0" in response.text


def test_metrics_returns_200_and_preserves_gauge_on_database_error(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")
    api.RETRAINING_REQUIRED.set(1)

    def unavailable_database():
        raise SQLAlchemyError("Database unavailable")

    monkeypatch.setattr(
        api,
        "create_database_engine",
        unavailable_database,
    )

    with TestClient(api.create_app()) as client:
        response = _get_metrics(client)

    assert response.status_code == 200
    assert "promiseops_retraining_required 1.0" in response.text
