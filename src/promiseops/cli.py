"""Command-line interface for PromiseOps."""

import argparse
import sys

from promiseops.contracts import instant


def main():
    parser = argparse.ArgumentParser(prog="promiseops")
    commands = parser.add_subparsers(dest="command", required=True)

    priority_parser = commands.add_parser(
        "score-priority",
        help="Score a cohort and select the highest-risk 10% of orders",
    )
    priority_parser.add_argument(
        "--as-of",
        required=True,
        help="Timezone-aware scoring cutoff, for example 2018-01-01T00:00:00Z",
    )

    args = parser.parse_args()

    if args.command == "score-priority":
        try:
            as_of = instant(args.as_of)
        except (TypeError, ValueError) as exc:
            parser.error(f"invalid --as-of: {exc}")

        print(
            "Priority scoring is not available: database and model integration "
            f"are not implemented (as_of={as_of.isoformat()}).",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
