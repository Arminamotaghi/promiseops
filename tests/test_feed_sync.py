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
                    "entries": [
                        {
                            "file_name": "batch1.json",
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
                    "entries": [
                        {
                            "file_name": "batch_fail.json",
                            "file_sha256": "sha_fail",
                            "payload_sha256": "payload_sha_fail",
                            "available_at": "2026-01-01T00:00:00Z",
                            "kind": "orders",
                        },
                        {
                            "file_name": "batch_success.json",
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
    output_data = json.loads(captured.out.strip())
    assert output_data["file_name"] == "batch_fail.json"
    assert output_data["status"] == "rejected"
    assert "checksum_mismatch" in output_data["reason"]
