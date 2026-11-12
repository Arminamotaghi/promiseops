"""Command-line interface for PromiseOps."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from promiseops.contracts import instant
from promiseops.release import build_baseline_bundle


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

    return None


if __name__ == "__main__":
    raise SystemExit(main())
