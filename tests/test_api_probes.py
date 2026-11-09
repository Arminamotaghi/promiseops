from fastapi.testclient import TestClient

from promiseops.api import create_app


def test_healthz_returns_200():
    client = TestClient(create_app())
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_200():
    client = TestClient(create_app())
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
