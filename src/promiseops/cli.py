"""Command-line interface for PromiseOps."""

import argparse
from datetime import datetime, timezone
import json
import os
import sys

from promiseops.contracts import instant


def main():
    parser = argparse.ArgumentParser(prog="promiseops")
    commands = parser.add_subparsers(dest="command", required=True)

    # 1. Subcommand: score-priority (existing, untouched)
    priority_parser = commands.add_parser(
        "score-priority",
        help="Score a cohort and select the highest-risk 10% of orders",
    )
    priority_parser.add_argument(
        "--as-of",
        required=False,
        help="Timezone-aware scoring cutoff, for example 2018-01-01T00:00:00Z",
    )

    # 2. Subcommand: ingest (new, transactional & idempotent batch ingestion)
    ingest_parser = commands.add_parser(
        "ingest",
        help="Ingest a JSON batch payload with idempotent transactional guarantees",
    )
    ingest_parser.add_argument(
        "--file",
        required=True,
        help="Path to the JSON batch payload file",
    )
    ingest_parser.add_argument(
        "--as-of",
        required=True,
        help="Timezone-aware cutoff timestamp for the feed, e.g., 2018-01-01T00:00:00Z",
    )
    ingest_parser.add_argument(
        "--expected-sha256",
        required=True,
        help="Expected SHA-256 checksum of the payload file",
    )

    args = parser.parse_args()

    # --- Handling: score-priority ---
    if args.command == "score-priority":
        try:
            as_of = (
                instant(args.as_of)
                if args.as_of is not None
                else datetime.now(timezone.utc)
            )
        except (TypeError, ValueError) as exc:
            parser.error(f"invalid --as-of: {exc}")

        print(
            "Priority scoring is not available: database and model integration "
            f"are not implemented (as_of={as_of.isoformat()}).",
            file=sys.stderr,
        )
        return 1

    # --- Handling: ingest ---
    elif args.command == "ingest":
        # 1. Validate and parse --as-of
        try:
            as_of_dt = instant(args.as_of)
        except (TypeError, ValueError) as exc:
            print(f"error: invalid --as-of format: {exc}", file=sys.stderr)
            return 1

        # 2. Validate file existence
        if not os.path.isfile(args.file):
            print(f"error: payload file not found: {args.file}", file=sys.stderr)
            return 1

        # 3. Read payload bytes
        try:
            with open(args.file, "rb") as f:
                payload_bytes = f.read()
        except OSError as exc:
            print(f"error: failed to read file {args.file}: {exc}", file=sys.stderr)
            return 1

        # 4. Check database connection string from environment
        database_url = os.environ.get("PROMISEOPS_DATABASE_URL")
        if not database_url:
            print(
                "error: PROMISEOPS_DATABASE_URL environment variable is not set.",
                file=sys.stderr,
            )
            return 1

        # 5. Lazy import of database driver and ingestion engine to protect score-priority
        try:
            import psycopg
            from promiseops.data import ingest_batch
        except ImportError as exc:
            print(
                f"error: required module/driver could not be imported: {exc}",
                file=sys.stderr,
            )
            return 1

        # 6. Execute transactional ingest
        try:
            with psycopg.connect(database_url) as connection:
                result = ingest_batch(
                    connection,
                    payload_bytes,
                    as_of=as_of_dt,
                    expected_sha256=args.expected_sha256,
                )
                # Successful ingestion receipt output on stdout
                print(json.dumps(result, indent=2))
                return 0
        except ValueError as exc:
            # Domain/validation errors raised by engine (already quarantined internally)
            reason_code = str(exc)
            print(f"ingest rejected with reason_code: {reason_code}", file=sys.stderr)
            return 1
        except Exception as exc:
            # Unexpected system/database errors
            print(f"error during ingestion transaction: {exc}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
