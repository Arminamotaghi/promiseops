import json
import httpx
import pytest

from promiseops.feed_sync import main, sort_feed_entries, sync_feed


class FakeConnection:
    def commit(self):
        pass

    def rollback(self):
        pass


def test_sort_feed_entries():
    entries = [
        {"file_name": "b.json", "available_at": "2026-01-02T00:00:00Z", "kind": "orders"},
        {"file_name": "a.json", "available_at": "2026-01-01T00:00:00Z", "kind": "outcomes"},
        {"file_name": "c.json", "available_at": "2026-01-01T00:00:00Z", "kind": "orders"},
    ]
    sorted_e = sort_feed_entries(entries)
    # Same timestamp: orders before outcomes. Earlier date first.
    assert sorted_e[0]["file_name"] == "c.json"  # 2026-01-01 orders
    assert sorted_e[1]["file_name"] == "a.json"  # 2026-01-01 outcomes
    assert sorted_e[2]["file_name"] == "b.json"  # 2026-01-02 orders


def test_sync_feed_success(monkeypatch):
    def handler(request: httpx.Request):
        if request.url.path.endswith("/feed"):
            return httpx.Response(
                200,
                json={
                    "as_of": "2026-01-03T00:00:00Z",
                    "releases": [
                        {
                            "file": "batch1.json",
                            "file_sha256": "dummy_sha",
                            "payload_sha256": "dummy_payload_sha",
                            "available_at": "2026-01-01T00:00:00Z",
                            "kind": "orders",
                        }
                    ]
                },
            )
        elif "/feed/" in request.url.path:
            return httpx.Response(200, content=b'{"schema_version":"1.0"}')
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    conn = FakeConnection()

    monkeypatch.setattr("promiseops.feed_sync.ingest_batch", lambda c, content, **kwargs: {"status": "accepted"})

    exit_code = sync_feed("http://gateway", "test-key", connection=conn, transport=transport)
    assert exit_code == 0


def test_main_missing_feed_key(monkeypatch):
    monkeypatch.delenv("PROMISEOPS_FEED_KEY", raising=False)
    exit_code = main(["--base-url", "http://gateway"])
    assert exit_code == 1


def test_sync_feed_stops_on_rejection(monkeypatch, capsys):
    downloaded_files = []

    def handler(request: httpx.Request):
        if request.url.path.endswith("/feed"):
            return httpx.Response(
                200,
                json={
                    "as_of": "2026-01-03T00:00:00Z",
                    "releases": [
                        {
                            "file": "batch_fail.json",
                            "file_sha256": "sha_fail",
                            "payload_sha256": "payload_sha_fail",
                            "available_at": "2026-01-01T00:00:00Z",
                            "kind": "orders",
                        },
                        {
                            "file": "batch_success.json",
                            "file_sha256": "sha_ok",
                            "payload_sha256": "payload_sha_ok",
                            "available_at": "2026-01-02T00:00:00Z",
                            "kind": "orders",
                        },
                    ]
                },
            )
        elif "/feed/" in request.url.path:
            path_part = request.url.path.split("/")[-1]
            downloaded_files.append(path_part)
            return httpx.Response(200, content=b'{"schema_version":"1.0"}')
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    conn = FakeConnection()

    def mock_ingest(c, content, **kwargs):
        if kwargs.get("file_name") == "batch_fail.json":
            raise ValueError("checksum_mismatch")
        return {"status": "accepted"}

    monkeypatch.setattr("promiseops.feed_sync.ingest_batch", mock_ingest)

    exit_code = sync_feed("http://gateway", "test-key", connection=conn, transport=transport)
    assert exit_code == 1

    # Ensure second batch was never downloaded because processing stopped at the first failure
    assert "batch_fail.json" in downloaded_files
    assert "batch_success.json" not in downloaded_files

    captured = capsys.readouterr()
    output_data = json.loads(captured.out.strip().splitlines()[-1])
    assert output_data["file_name"] == "batch_fail.json"
    assert output_data["status"] == "rejected"
    assert "checksum_mismatch" in output_data["reason"]

def test_main_missing_database_url(monkeypatch):
    monkeypatch.setenv("PROMISEOPS_FEED_KEY", "test-key")
    monkeypatch.delenv("PROMISEOPS_DATABASE_URL", raising=False)

    assert main(["--base-url", "http://gateway"]) == 1


def test_main_opens_database_connection(monkeypatch):
    calls = {}
    conn = FakeConnection()

    class ConnectionContext:
        def __enter__(self):
            return conn

        def __exit__(self, exc_type, exc, traceback):
            calls["closed"] = True

    def fake_connect(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return ConnectionContext()

    monkeypatch.setenv("PROMISEOPS_FEED_KEY", "test-key")
    monkeypatch.setenv(
        "PROMISEOPS_DATABASE_URL",
        "postgresql+psycopg://user:password@db/promiseops",
    )
    monkeypatch.setattr("promiseops.feed_sync.psycopg.connect", fake_connect)
    monkeypatch.setattr("promiseops.feed_sync.sync_feed", lambda *args, **kwargs: (
        calls.update(sync_connection=kwargs["connection"]) or 0
    ))

    assert main(["--base-url", "http://gateway"]) == 0
    assert calls["url"] == "postgresql://user:password@db/promiseops"
    assert calls["kwargs"] == {"autocommit": True, "connect_timeout": 15}
    assert calls["sync_connection"] is conn
    assert calls["closed"] is True


def test_main_database_connection_error_returns_one(monkeypatch):
    import psycopg

    monkeypatch.setenv("PROMISEOPS_FEED_KEY", "test-key")
    monkeypatch.setenv("PROMISEOPS_DATABASE_URL", "postgresql://user:password@db/promiseops")

    def fail_connect(*args, **kwargs):
        raise psycopg.OperationalError("database unavailable")

    monkeypatch.setattr("promiseops.feed_sync.psycopg.connect", fail_connect)

    assert main(["--base-url", "http://gateway"]) == 1

def test_sync_feed_missing_required_fields_returns_one(capsys):
    """Ensure that feed sync stops with exit code 1 instead of silently skipping when a required field like file_name/file or file_sha256 is missing."""
    
    def handler(request: httpx.Request):
        if request.url.path.endswith("/feed"):
            return httpx.Response(
                200,
                json={
                    "as_of": "2026-01-03T00:00:00Z",
                    "entries": [
                        {
                            "file_sha256": "abc123sha",
                            "available_at": "2026-01-01T00:00:00Z",
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    ret = sync_feed(
        "http://gateway.test",
        "test-feed-key",
        connection=FakeConnection(),
        transport=transport,
    )

    assert ret == 1
    assert "incomplete feed release" in capsys.readouterr().err


# --- the real gateway manifest: {"schema_version", "as_of", "releases": [...]} ------------------

def _manifest(releases, as_of="2018-02-01T00:00:00+00:00"):
    return {"schema_version": "1.0", "as_of": as_of, "releases": releases}


def _release(name, available_at, kind="orders"):
    return {"file": name, "available_at": available_at, "kind": kind, "file_sha256": "f" * 64, "payload_sha256": "p" * 64}


def _gateway(manifest, downloaded):
    def handler(request: httpx.Request):
        if request.url.path.endswith("/feed"):
            return httpx.Response(200, json=manifest)
        downloaded.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, content=b"{}")

    return httpx.MockTransport(handler)


def test_releases_up_to_the_requested_cutoff_only_in_order(monkeypatch):
    downloaded, calls = [], []
    manifest = _manifest([
        _release("outcomes-20180115.json", "2018-01-15T00:00:00+00:00", "outcomes"),
        _release("orders-20180129.json", "2018-01-29T00:00:00+00:00"),
        _release("orders-20180115.json", "2018-01-15T00:00:00+00:00"),
        _release("orders-20180101.json", "2018-01-01T00:00:00+00:00"),
    ])
    monkeypatch.setattr("promiseops.feed_sync.ingest_batch", lambda c, content, **kw: calls.append(kw) or {"status": "accepted"})
    assert sync_feed("http://gw", "k", as_of="2018-01-15T00:00:00Z", connection=FakeConnection(), transport=_gateway(manifest, downloaded)) == 0
    assert downloaded == ["orders-20180101.json", "orders-20180115.json", "outcomes-20180115.json"]
    assert {kw["as_of"].isoformat() for kw in calls} == {"2018-01-15T00:00:00+00:00"}


def test_without_as_of_the_manifest_as_of_is_the_cutoff(monkeypatch):
    downloaded = []
    manifest = _manifest([_release("orders-20180101.json", "2018-01-01T00:00:00+00:00"), _release("orders-20180212.json", "2018-02-12T00:00:00+00:00")])
    monkeypatch.setattr("promiseops.feed_sync.ingest_batch", lambda c, content, **kw: {"status": "accepted"})
    assert sync_feed("http://gw", "k", connection=FakeConnection(), transport=_gateway(manifest, downloaded)) == 0
    assert downloaded == ["orders-20180101.json"]


@pytest.mark.parametrize("manifest", [
    {"schema_version": "1.0", "as_of": "2018-02-01T00:00:00+00:00", "batches": []},
    _manifest([]),
    _manifest([_release("orders-20180301.json", "2018-03-01T00:00:00+00:00")]),
])
def test_nothing_to_ingest_is_an_error_not_a_silent_success(manifest, capsys):
    downloaded = []
    assert sync_feed("http://gw", "k", connection=FakeConnection(), transport=_gateway(manifest, downloaded)) == 1
    assert downloaded == [] and "Error" in capsys.readouterr().err

