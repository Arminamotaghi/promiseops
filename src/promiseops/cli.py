"""Command-line interface for PromiseOps."""

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from promiseops.contracts import instant
from promiseops.release import build_baseline_bundle
import psycopg
from promiseops.data import ingest_batch


def latest_released_at():
    """Get the latest order release timestamp for replay scoring."""
    database_url = os.environ.get("PROMISEOPS_DATABASE_URL")
    if not database_url:
        raise ValueError(
            "PROMISEOPS_DATABASE_URL is required when --as-of is omitted"
        )

    try:
        if database_url.startswith("postgresql://"):
            database_url = (
                "postgresql+psycopg://"
                + database_url[len("postgresql://"):]
            )
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                value = connection.execute(
                    text("SELECT MAX(released_at) FROM orders")
                ).scalar_one()
        finally:
            engine.dispose()
    except SQLAlchemyError as exc:
        raise ValueError(
            "Cannot read latest released_at from orders database"
        ) from exc

    if value is None:
        raise ValueError("No orders found in database")

    return instant(value.isoformat())


def main(argv: list[str] | None = None) -> int | None:
    parser = argparse.ArgumentParser(prog="promiseops")
    commands = parser.add_subparsers(dest="command", required=True)

    priority_parser = commands.add_parser(
        "score-priority",
        help="Score a cohort and select the highest-risk 10% of orders",
    )
    priority_parser.add_argument(
        "--as-of",
        required=False,
        help="Timezone-aware scoring cutoff, for example 2018-01-01T00:00:00Z",
    )

    baseline = commands.add_parser(
        "make-baseline",
        help="Build the bootstrap release bundle from synthetic fixtures",
    )
    baseline.add_argument("--out", type=Path, default=Path("release"))
    baseline.add_argument("--fixtures", type=Path, default=Path("fixtures"))

    # --- ADDED FOR INGEST: Subcommand Definition ---
    ingest_parser = commands.add_parser(
        "ingest",
        help="Ingest order batch payload into database",
    )
    ingest_parser.add_argument(
        "--file",
        required=True,
        help="Path to JSON payload file",
    )
    ingest_parser.add_argument(
        "--as-of",
        required=True,
        help="Ingestion timestamp ISO-8601 (e.g. 2026-03-31T23:59:59)",
    )
    ingest_parser.add_argument(
        "--expected-sha256",
        required=True,
        help="Expected SHA-256 checksum of the payload file",
    )
    # -----------------------------------------------

    args = parser.parse_args(argv)

    if args.command == "score-priority":
        try:
            as_of = (
                instant(args.as_of)
                if args.as_of is not None
                else latest_released_at()

            )
        except (TypeError, ValueError) as exc:
            if args.as_of is not None:
                parser.error(f"invalid --as-of: {exc}")
            parser.error(str(exc))

        from promiseops.priority_job import score_priority

        try:
            output = score_priority(as_of=as_of)
        except Exception as exc:
            print(
                f"Priority scoring failed: {type(exc).__name__}",
                file=sys.stderr,
            )
            return 1

        print(json.dumps(output, indent=2, sort_keys=True))
        return 0

    if args.command == "make-baseline":
        metadata = build_baseline_bundle(args.out, fixtures_dir=args.fixtures)
        print(json.dumps(metadata, indent=2, sort_keys=True))
        return 0 # اضافه شد برای اطمینان از خروج موفق

    # --- ADDED FOR INGEST: Command Handling ---
    if args.command == "ingest":
        try:
            as_of = instant(args.as_of)
        except (TypeError, ValueError) as exc:
            print(f"error: invalid --as-of format: {exc}", file=sys.stderr)
            return 1

        if not os.path.isfile(args.file):
            print(f"error: payload file not found: {args.file}", file=sys.stderr)
            return 1

        database_url = os.environ.get("PROMISEOPS_DATABASE_URL")
        if not database_url:
            print(
                "error: PROMISEOPS_DATABASE_URL is not configured",
                file=sys.stderr,
            )
            return 1

        payload = Path(args.file).read_bytes()

        with psycopg.connect(database_url) as connection:
            res = ingest_batch(
                connection,
                payload,
                as_of=as_of,
                expected_sha256=args.expected_sha256,
                file_name=Path(args.file).name,
            )

        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("status") in (
            "accepted",
            "success",
            "replayed",
        ) else 1
    # ------------------------------------------

    return None


if __name__ == "__main__":
    raise SystemExit(main())
