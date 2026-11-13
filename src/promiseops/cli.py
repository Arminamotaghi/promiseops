"""Command-line interface for PromiseOps."""

import argparse
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from promiseops.contracts import instant


def latest_released_at():
    """Get the latest order release timestamp for replay scoring."""
    database_url = os.environ.get("PROMISEOPS_DATABASE_URL")
    if not database_url:
        raise ValueError(
            "PROMISEOPS_DATABASE_URL is required when --as-of is omitted"
        )

    try:
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


def main():
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

    args = parser.parse_args()

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

        print(
            "Priority scoring is not available: database and model integration "
            f"are not implemented (as_of={as_of.isoformat()}).",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
