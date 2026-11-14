"""Command-line interface for PromiseOps."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from promiseops.contracts import instant
from promiseops.release import build_baseline_bundle
from promiseops.data import ingest_batch 


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
                else datetime.now(UTC)
            )
        except (TypeError, ValueError) as exc:
            parser.error(f"invalid --as-of: {exc}")

        print(
            "Priority scoring is not available: database and model integration "
            f"are not implemented (as_of={as_of.isoformat()}).",
            file=sys.stderr,
        )
        return 1

    if args.command == "make-baseline":
        metadata = build_baseline_bundle(args.out, fixtures_dir=args.fixtures)
        print(json.dumps(metadata, indent=2, sort_keys=True))
        return 0 # اضافه شد برای اطمینان از خروج موفق

    # --- ADDED FOR INGEST: Command Handling ---
    if args.command == "ingest":
        try:
            # 1. Validate --as-of format
            instant(args.as_of)
        except (TypeError, ValueError) as exc:
            print(f"error: invalid --as-of format: {exc}", file=sys.stderr)
            return 1

        # 2. Validate file existence
        if not os.path.isfile(args.file):
            print(f"error: payload file not found: {args.file}", file=sys.stderr)
            return 1

        # 3. Execute ingestion
        res = ingest_batch(
            file_path=args.file,
            as_of_str=args.as_of,
            expected_sha256=args.expected_sha256,
        )
        
        # 4. Output result and exit code
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("status") in ("success", "replayed") else 1
    # ------------------------------------------

    return None


if __name__ == "__main__":
    raise SystemExit(main())
