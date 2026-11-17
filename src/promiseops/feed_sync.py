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
import psycopg

from promiseops.data import ingest_batch
from promiseops.gateway import download_feed_file, get_feed_index
from promiseops.migrate import connection_url


def sort_feed_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort feed entries by available_at datetime ascending, with orders before outcomes."""
    def sort_key(entry: dict[str, Any]) -> tuple[datetime, int]:
        available_at_str = entry.get("available_at", "")
        # Parse timestamp to ensure correct chronological sorting
        try:
            available_at = datetime.fromisoformat(available_at_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            available_at = datetime.min
            
        kind = entry.get("kind", "")
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

    # Extract and parse the feed-level as_of timestamp from the index response
    feed_as_of_str = index_data.get("as_of")
    if not feed_as_of_str:
        print("Error: Feed index is missing 'as_of'.", file=sys.stderr)
        return 1

    try:
        feed_as_of_dt = datetime.fromisoformat(feed_as_of_str.replace("Z", "+00:00"))
    except ValueError as exc:
        print(f"Error: Invalid feed as_of timestamp '{feed_as_of_str}': {exc}", file=sys.stderr)
        return 1

    entries = index_data.get("entries", [])
    sorted_entries = sort_feed_entries(entries)

    for entry in sorted_entries:
        file_name = entry.get("file_name") or entry.get("file")
        file_sha256 = entry.get("file_sha256")
        payload_sha256 = entry.get("payload_sha256")
        available_at_str = entry.get("available_at")

        if not file_name or not file_sha256 or not available_at_str:
            print(f"Error: Invalid or incomplete feed entry (missing file/file_name, checksum, or available_at): {entry}", file=sys.stderr)
            return 1

    try:
        index_data = get_feed_index(base_url, feed_key, as_of=as_of, transport=transport)
    except Exception as e:
        print(f"Error fetching feed index: {e}", file=sys.stderr)
        return 1

    feed_as_of_str = index_data.get("as_of")
    if not feed_as_of_str:
        print("Error: Feed index is missing 'as_of'.", file=sys.stderr)
        return 1

    try:
        feed_as_of_dt = datetime.fromisoformat(feed_as_of_str.replace("Z", "+00:00"))
    except ValueError as exc:
        print(f"Error: Invalid feed as_of timestamp '{feed_as_of_str}': {exc}", file=sys.stderr)
        return 1

    entries = index_data.get("entries", [])
    sorted_entries = sort_feed_entries(entries)

    for entry in sorted_entries:
        file_name = entry.get("file_name") or entry.get("file")
        file_sha256 = entry.get("file_sha256")
        payload_sha256 = entry.get("payload_sha256")
        available_at_str = entry.get("available_at")

        if not file_name or not file_sha256 or not available_at_str:
            print(f"Error: Invalid or incomplete feed entry (missing file/file_name, checksum, or available_at): {entry}", file=sys.stderr)
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
                as_of=feed_as_of_dt,
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
    parser.add_argument("--base-url", help="Gateway base URL")
    parser.add_argument("--as-of", help="Optional ISO cutoff timestamp")
    args = parser.parse_args(argv)

    feed_key = os.getenv("COMPANY_FEED_KEY") or os.getenv("PROMISEOPS_FEED_KEY")
    if not feed_key:
        print("Error: COMPANY_FEED_KEY (or PROMISEOPS_FEED_KEY) environment variable is not set.", file=sys.stderr)
        return 1

    base_url = args.base_url or os.getenv("COMPANY_GATEWAY_URL")
    if not base_url:
        print("Error: --base-url or COMPANY_GATEWAY_URL environment variable is not set.", file=sys.stderr)
        return 1


    database_url = os.getenv("PROMISEOPS_DATABASE_URL")
    if not database_url:
        print("Error: PROMISEOPS_DATABASE_URL environment variable is not set.", file=sys.stderr)
        return 1

    try:
        with psycopg.connect(
            connection_url(database_url),
            autocommit=True,
            connect_timeout=15,
        ) as connection:
            return sync_feed(
                base_url,
                feed_key,
                as_of=args.as_of,
                connection=connection,
            )
    except psycopg.Error as exc:
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        print(f"Error connecting to database: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
