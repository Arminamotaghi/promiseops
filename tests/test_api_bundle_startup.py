from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from promiseops import api


def test_valid_bundle_and_ready_database_returns_200(monkeypatch):
    model = object()
    bundle = SimpleNamespace(
        model=model,
        metadata={"model_version": "test-v1"},
    )
    loader = MagicMock(return_value=bundle)
    monkeypatch.setattr(api, "load_bundle", loader)
    monkeypatch.setenv("PROMISEOPS_RELEASE_DIR", "/tmp/test-release")

    client = TestClient(api.create_app(database_ready=lambda: True))

    assert client.get("/readyz").status_code == 200
    assert client.app.state.model is model
    loader.assert_called_once_with("/tmp/test-release")


def test_missing_bundle_returns_503(monkeypatch):
    monkeypatch.setattr(
        api,
        "load_bundle",
        MagicMock(side_effect=api.BundleError("bundle missing")),
    )

    client = TestClient(api.create_app(database_ready=lambda: True))

    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 503


def test_disconnected_database_returns_503(monkeypatch):
    bundle = SimpleNamespace(
        model=object(),
        metadata={"model_version": "test-v2"},
    )
    monkeypatch.setattr(api, "load_bundle", lambda _: bundle)
    monkeypatch.setattr(api, "check_database_ready", lambda: False)

    client = TestClient(api.create_app())

    assert client.get("/readyz").status_code == 503


def test_database_readiness_executes_select_one(monkeypatch):
    monkeypatch.setenv(
        "PROMISEOPS_DATABASE_URL",
        "postgresql://user:pass@localhost:5432/promiseops",
    )

    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    create_engine = MagicMock(return_value=engine)
    monkeypatch.setattr(api, "create_engine", create_engine)

    assert api.check_database_ready() is True
    assert create_engine.call_args.args[0].startswith("postgresql+psycopg://")
    assert str(connection.execute.call_args.args[0]) == "SELECT 1"
    engine.dispose.assert_called_once()


def test_model_version_metric_is_exposed(monkeypatch):
    bundle = SimpleNamespace(
        model=object(),
        metadata={"model_version": "test-v3"},
    )
    monkeypatch.setattr(api, "load_bundle", lambda _: bundle)
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")

    client = TestClient(api.create_app(database_ready=lambda: True))
    response = client.get(
        "/metrics",
        headers={"X-API-Key": "test-secret"},
    )

    assert response.status_code == 200
    assert 'promiseops_model_info{model_version="test-v3"} 1.0' in response.text
