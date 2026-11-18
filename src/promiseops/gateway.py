import httpx
import re
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
    timeout: float = 30.0,
    transport=None,
) -> bytes:
    """
    Download a feed file from the internal gateway via GET /feed/{file_name}.
    """
    # Security check for file_name to prevent path injection
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", file_name):
        raise ValueError(f"Invalid file name: {file_name}")

    url = f"{base_url.rstrip('/')}/feed/{file_name}"
    headers = {
        "Authorization": f"Bearer {feed_key}"
    }
    
    client_kwargs = {"timeout": timeout}
    if transport is not None:
        client_kwargs["transport"] = transport

    with httpx.Client(**client_kwargs) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        return response.content
