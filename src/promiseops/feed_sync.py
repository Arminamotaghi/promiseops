"""Feed synchronization client for PromiseOps.

Fetches feed index from gateway, sorts entries chronologically (with orders
before outcomes at equal timestamps), downloads files, and ingests them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

import httpx

from promiseops.data import ingest_batch
from promiseops.gateway import download_feed_file, get_feed_index


def sort_feed_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort feed entries by available_at ascending, with orders before outcomes."""
    def sort_key(entry: dict[str, Any]) -> tuple[str, int]:
        available_at = entry.get("available_at", "")
        kind = entry.get("kind", "")
        # Orders (0) should come before outcomes (1) at the same timestamp
        kind_priority = 0 if kind == "orders" else 1
        return (available_at, kind_priority)

    return sorted(entries, key=sort_key)


def sync_feed(
    base_url: str,
    feed_key: str,
    as_of: str | None = None,
    connection: Any = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Synchronize feed entries from gateway and ingest them."""
    try:
        index_data = get_feed_index(base_url, feed_key, as_of=as_of, transport=transport)
    except Exception as e:
        print(f"Error fetching feed index: {e}", file=sys.stderr)
        return 1

    entries = index_data.get("entries", [])
    sorted_entries = sort_feed_entries(entries)

    for entry in sorted_entries:
        file_name = entry.get("file_name")
        file_sha256 = entry.get("file_sha256")
        payload_sha256 = entry.get("payload_sha256")
        available_at_str = entry.get("available_at")

        if not file_name or not file_sha256 or not available_at_str:
            print(f"Skipping invalid feed entry: {entry}", file=sys.stderr)
            continue

        try:
            as_of_dt = datetime.fromisoformat(available_at_str.replace("Z", "+00:00"))
        except ValueError as exc:
            print(f"Invalid timestamp in entry {file_name}: {exc}", file=sys.stderr)
            return 1

        try:
            content = download_feed_file(base_url, feed_key, file_name, transport=transport)
        except Exception as e:
            reason = f"download_failed: {e}"
            print(json.dumps({"file_name": file_name, "status": "rejected", "reason": reason}))
            return 1

        try:
            result = ingest_batch(
                connection,
                content,
                as_of=as_of_dt,
                expected_sha256=file_sha256,
                expected_payload_sha256=payload_sha256,
                file_name=file_name,
            )
            print(json.dumps({"file_name": file_name, "status": "success", "result": result}))
        except Exception as e:
            reason_msg = str(e)
            print(json.dumps({"file_name": file_name, "status": "rejected", "reason": reason_msg}))
            return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="promiseops.feed_sync")
    parser.add_argument("--base-url", required=True, help="Gateway base URL")
    parser.add_argument("--as-of", help="Optional ISO cutoff timestamp")
    args = parser.parse_args(argv)

    feed_key = os.getenv("PROMISEOPS_FEED_KEY")
    if not feed_key:
        print("Error: PROMISEOPS_FEED_KEY environment variable is not set.", file=sys.stderr)
        return 1

    # In production, a real database connection would be provided.
    # For CLI execution without full DB config here, we pass None or a connection handle.
    return sync_feed(args.base_url, feed_key, as_of=args.as_of, connection=None)


if __name__ == "__main__":
    raise SystemExit(main())
