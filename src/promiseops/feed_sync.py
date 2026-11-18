"""Feed synchronization client for PromiseOps.

Fetches the feed manifest from the gateway, keeps the releases up to the cutoff, sorts them
chronologically (orders before outcomes at equal timestamps), downloads and ingests them.
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


def _releases(index_data: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The batch list of a manifest: the gateway calls it ``releases``; ``entries`` is accepted too."""
    for key in ("releases", "entries"):
        value = index_data.get(key)
        if isinstance(value, list):
            return value
    return None


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp without timezone")
    return parsed


def sync_feed(
    base_url: str,
    feed_key: str,
    as_of: str | None = None,
    connection: Any = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Synchronize feed releases from the gateway and ingest them in order.

    The gateway answers ``GET /feed`` with ``{"schema_version", "as_of", "releases": [...]}``;
    each release has ``file``, ``available_at``, ``kind``, ``file_sha256`` and
    ``payload_sha256``. Only releases with ``available_at <= cutoff`` are ingested, where the
    cutoff is the requested ``as_of`` (replay) or, without one, the manifest ``as_of``. The
    cutoff is also the ``as_of`` stored with every receipt. A manifest without a release list,
    or with nothing up to the cutoff, is an error: a silent "success" that ingested nothing
    would hide a broken feed.
    """
    try:
        index_data = get_feed_index(base_url, feed_key, as_of=as_of, transport=transport)
    except Exception as e:
        print(f"Error fetching feed index: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    releases = _releases(index_data)
    if releases is None:
        print(f"Error: feed manifest has no release list (keys: {sorted(index_data)}).", file=sys.stderr)
        return 1

    cutoff_str = as_of or index_data.get("as_of")
    if not cutoff_str:
        print("Error: no as_of given and the feed manifest has none.", file=sys.stderr)
        return 1
    try:
        cutoff = _instant(cutoff_str)
    except ValueError as exc:
        print(f"Error: invalid as_of '{cutoff_str}': {exc}", file=sys.stderr)
        return 1

    selected = []
    for entry in releases:
        file_name = entry.get("file") or entry.get("file_name")
        if not file_name or not entry.get("file_sha256") or not entry.get("available_at"):
            print(f"Error: incomplete feed release (needs file, file_sha256, available_at): {entry}", file=sys.stderr)
            return 1
        try:
            available_at = _instant(entry["available_at"])
        except ValueError as exc:
            print(f"Error: invalid available_at for {file_name}: {exc}", file=sys.stderr)
            return 1
        if available_at <= cutoff:
            selected.append(entry)

    if not selected:
        print(f"Error: no feed release available up to {cutoff.isoformat()} ({len(releases)} in the manifest).", file=sys.stderr)
        return 1

    print(json.dumps({"cutoff": cutoff.isoformat(), "releases_in_manifest": len(releases), "releases_to_ingest": len(selected)}))
    for entry in sort_feed_entries(selected):
        file_name = entry.get("file") or entry.get("file_name")
        try:
            content = download_feed_file(base_url, feed_key, file_name, transport=transport)
        except Exception as e:
            print(json.dumps({"file_name": file_name, "status": "rejected", "reason": f"download_failed: {type(e).__name__}"}))
            return 1

        try:
            result = ingest_batch(
                connection,
                content,
                as_of=cutoff,
                expected_sha256=entry["file_sha256"],
                expected_payload_sha256=entry.get("payload_sha256"),
                file_name=file_name,
            )
        except Exception as e:
            print(json.dumps({"file_name": file_name, "status": "rejected", "reason": str(e)}))
            return 1
        print(json.dumps({"file_name": file_name, "status": "success", "result": result}, default=str))

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
