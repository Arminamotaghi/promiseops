from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from promiseops import api


def test_predict_returns_404_for_missing_order(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")

    bundle = SimpleNamespace(
        model=object(),
        metadata={"model_version": "test-v1"},
    )
    monkeypatch.setattr(api, "load_bundle", lambda _: bundle)

    engine = MagicMock()

    monkeypatch.setattr(api, "create_database_engine", lambda: engine)
    monkeypatch.setattr(api, "get_existing_prediction", lambda conn, rid: None)
    monkeypatch.setattr(api, "calculate_order_risk", lambda conn, model, oid: None)

    client = TestClient(api.create_app(database_ready=lambda: True))

    response = client.post(
        "/predict",
        headers={"X-API-Key": "test-secret"},
        json={"order_id": "missing-order", "request_id": "req-404"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "order_not_found",
            "message": "Order was not found",
        }
    }
    engine.begin.assert_called_once()


def test_predict_returns_200_and_saves_prediction(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")

    bundle = SimpleNamespace(
        model=object(),
        metadata={"model_version": "test-v1"},
    )
    monkeypatch.setattr(api, "load_bundle", lambda _: bundle)

    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value

    monkeypatch.setattr(api, "create_database_engine", lambda: engine)
    monkeypatch.setattr(api, "get_existing_prediction", lambda conn, rid: None)

    calculate = MagicMock(return_value=0.75)
    monkeypatch.setattr(api, "calculate_order_risk", calculate)

    saved_row = {
        "order_id": "order-123",
        "request_id": "req-200",
        "risk_probability": 0.75,
        "model_version": "test-v1",
    }

    connection.execute.return_value.mappings.return_value.first.return_value = saved_row

    client = TestClient(api.create_app(database_ready=lambda: True))

    response = client.post(
        "/predict",
        headers={"X-API-Key": "test-secret"},
        json={"order_id": "order-123", "request_id": "req-200"},
    )

    assert response.status_code == 200
    assert response.json() == saved_row

    calculate.assert_called_once()
    engine.begin.assert_called_once()

    statement, parameters = connection.execute.call_args.args

    assert "INSERT INTO predictions" in str(statement)
    assert parameters["request_id"] == "req-200"
    assert parameters["order_id"] == "order-123"
    assert parameters["risk_probability"] == 0.75
    assert parameters["model_version"] == "test-v1"


def test_predict_reuses_existing_prediction(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")

    bundle = SimpleNamespace(
        model=object(),
        metadata={"model_version": "test-v2"},
    )
    monkeypatch.setattr(api, "load_bundle", lambda _: bundle)

    engine = MagicMock()
    monkeypatch.setattr(api, "create_database_engine", lambda: engine)

    previous = {
        "order_id": "order-123",
        "request_id": "req-repeat",
        "risk_probability": 0.35,
        "model_version": "test-v1",
    }

    lookup = MagicMock(return_value=previous)
    calculate = MagicMock()

    monkeypatch.setattr(api, "get_existing_prediction", lookup)
    monkeypatch.setattr(api, "calculate_order_risk", calculate)

    client = TestClient(api.create_app(database_ready=lambda: True))

    response = client.post(
        "/predict",
        headers={"X-API-Key": "test-secret"},
        json={"order_id": "order-123", "request_id": "req-repeat"},
    )

    assert response.status_code == 200
    assert response.json() == previous
    lookup.assert_called_once()
    calculate.assert_not_called()

    connection = engine.begin.return_value.__enter__.return_value
    connection.execute.assert_not_called()


def test_predict_returns_409_for_conflicting_request_id(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")

    bundle = SimpleNamespace(
        model=object(),
        metadata={"model_version": "test-v1"},
    )
    monkeypatch.setattr(api, "load_bundle", lambda _: bundle)

    engine = MagicMock()
    monkeypatch.setattr(api, "create_database_engine", lambda: engine)

    previous = {
        "order_id": "order-original",
        "request_id": "req-conflict",
        "risk_probability": 0.42,
        "model_version": "test-v1",
    }

    monkeypatch.setattr(
        api,
        "get_existing_prediction",
        lambda conn, rid: previous,
    )

    calculate = MagicMock()
    monkeypatch.setattr(api, "calculate_order_risk", calculate)

    client = TestClient(api.create_app(database_ready=lambda: True))

    response = client.post(
        "/predict",
        headers={"X-API-Key": "test-secret"},
        json={"order_id": "order-different", "request_id": "req-conflict"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "request_id_conflict"
    calculate.assert_not_called()

    connection = engine.begin.return_value.__enter__.return_value
    connection.execute.assert_not_called()


def test_database_engine_disposed_on_shutdown(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")

    bundle = SimpleNamespace(
        model=object(),
        metadata={"model_version": "test-v1"},
    )
    monkeypatch.setattr(api, "load_bundle", lambda _: bundle)

    engine = MagicMock()
    create_engine_mock = MagicMock(return_value=engine)
    monkeypatch.setattr(
        api,
        "create_database_engine",
        create_engine_mock,
    )

    monkeypatch.setattr(
        api,
        "get_existing_prediction",
        lambda conn, rid: None,
    )
    monkeypatch.setattr(
        api,
        "calculate_order_risk",
        lambda conn, model, oid: None,
    )

    app = api.create_app(database_ready=lambda: True)

    with TestClient(app) as client:
        response = client.post(
            "/predict",
            headers={"X-API-Key": "test-secret"},
            json={"order_id": "order-123", "request_id": "req-shutdown"},
        )
        assert response.status_code == 404
        create_engine_mock.assert_called_once()
        engine.dispose.assert_not_called()

    engine.dispose.assert_called_once()
    assert app.state.database_engine is None


def test_predict_reuses_row_after_insert_conflict(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")

    bundle = SimpleNamespace(
        model=object(),
        metadata={"model_version": "test-v2"},
    )
    monkeypatch.setattr(api, "load_bundle", lambda _: bundle)

    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    monkeypatch.setattr(api, "create_database_engine", lambda: engine)

    saved_row = {
        "order_id": "order-123",
        "request_id": "req-race",
        "risk_probability": 0.25,
        "model_version": "test-v1",
    }

    lookup = MagicMock(side_effect=[None, saved_row])
    monkeypatch.setattr(api, "get_existing_prediction", lookup)
    monkeypatch.setattr(
        api,
        "calculate_order_risk",
        lambda conn, model, oid: 0.80,
    )

    connection.execute.return_value.mappings.return_value.first.return_value = None

    client = TestClient(api.create_app(database_ready=lambda: True))

    response = client.post(
        "/predict",
        headers={"X-API-Key": "test-secret"},
        json={"order_id": "order-123", "request_id": "req-race"},
    )

    assert response.status_code == 200
    assert response.json() == saved_row
    assert lookup.call_count == 2
    connection.execute.assert_called_once()


def test_predict_returns_409_after_insert_conflict(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")

    bundle = SimpleNamespace(
        model=object(),
        metadata={"model_version": "test-v2"},
    )
    monkeypatch.setattr(api, "load_bundle", lambda _: bundle)

    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    monkeypatch.setattr(api, "create_database_engine", lambda: engine)

    competing_row = {
        "order_id": "order-other",
        "request_id": "req-race",
        "risk_probability": 0.25,
        "model_version": "test-v1",
    }

    lookup = MagicMock(side_effect=[None, competing_row])
    monkeypatch.setattr(api, "get_existing_prediction", lookup)
    monkeypatch.setattr(
        api,
        "calculate_order_risk",
        lambda conn, model, oid: 0.80,
    )

    connection.execute.return_value.mappings.return_value.first.return_value = None

    client = TestClient(api.create_app(database_ready=lambda: True))

    response = client.post(
        "/predict",
        headers={"X-API-Key": "test-secret"},
        json={"order_id": "order-123", "request_id": "req-race"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "request_id_conflict"
    assert lookup.call_count == 2
    connection.execute.assert_called_once()


def test_predict_database_error_returns_safe_500(monkeypatch):
    from sqlalchemy.exc import OperationalError

    monkeypatch.setenv("PROMISEOPS_API_KEY", "test-secret")

    bundle = SimpleNamespace(
        model=object(),
        metadata={"model_version": "test-v1"},
    )
    monkeypatch.setattr(api, "load_bundle", lambda _: bundle)

    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value

    connection.execute.side_effect = OperationalError(
        "SELECT secret FROM predictions",
        {},
        Exception("private database details"),
    )

    monkeypatch.setattr(api, "create_database_engine", lambda: engine)

    client = TestClient(api.create_app(database_ready=lambda: True))

    response = client.post(
        "/predict",
        headers={"X-API-Key": "test-secret"},
        json={"order_id": "order-123", "request_id": "req-error"},
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "private database details" not in response.text
    assert "SELECT secret" not in response.text
