import httpx
from typing import Dict, Any, Optional, Tuple

def get_feed_index(
    base_url: str,
    feed_key: str,
    as_of: Optional[str] = None,
    timeout: float = 30.0,
    transport=None
) -> Dict[str, Any]:
    """
    Fetch the feed index from the internal gateway via GET /feed.
    
    The as_of parameter handles replay timestamps; httpx handles URL encoding 
    for characters like '+'.
    """
    url = f"{base_url.rstrip('/')}/feed"
    headers = {
        "Authorization": f"Bearer {feed_key}",
        "Accept": "application/json"
    }
    params = {"as_of": as_of} if as_of else {}

    with httpx.Client(headers=headers, timeout=timeout, transport=transport) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()

def download_feed_file(
    base_url: str,
    feed_key: str,
    file_name: str,
    expected_file_sha256: str,
    expected_payload_sha256: str,
    timeout: float = 30.0,
    transport=None,
) -> Tuple[bytes, str, str]:
    """
    Download a feed file. 
    
    Returns raw bytes and expected checksums to be processed by ingest_batch 
    (which calls ingest_checks.read_envelope for strict validation).
    """
    url = f"{base_url.rstrip('/')}/feed/{file_name}"
    headers = {
        "Authorization": f"Bearer {feed_key}"
    }

    with httpx.Client(headers=headers, timeout=timeout, transport=transport) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.content, expected_file_sha256, expected_payload_sha256
