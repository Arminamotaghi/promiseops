import pytest
import httpx
from promiseops.gateway import get_feed_index, download_feed_file


# Gateway Tests

def test_get_feed_index_success():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"entries": []}))
    assert get_feed_index("http://a", "k", transport=transport) == {"entries": []}

def test_get_feed_index_unauthorized():
    transport = httpx.MockTransport(lambda req: httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError):
        get_feed_index("http://a", "k", transport=transport)

def test_download_feed_file_not_found():
    transport = httpx.MockTransport(lambda req: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        download_feed_file("http://a", "k", "f", transport=transport)

def test_download_feed_file_returns_file_bytes():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"data"))
    content = download_feed_file(
    "http://a", "k", "f", transport=transport
)
    assert content == b"data"

def test_get_feed_index_with_as_of():
    def handler(request: httpx.Request) -> httpx.Response:
        # Check that the as_of parameter is correctly passed in the query string
        assert "as_of=2026-11-26" in str(request.url)
        return httpx.Response(200, json={"entries": [{"id": 1}]})

    transport = httpx.MockTransport(handler)
    result = get_feed_index("http://test-gateway", "dummy-key", as_of="2026-11-26", transport=transport)
    assert result == {"entries": [{"id": 1}]}
