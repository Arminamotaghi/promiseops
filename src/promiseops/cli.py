import argparse
import json
from pathlib import Path

from promiseops.release import build_baseline_bundle


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="promiseops")
    subparsers = parser.add_subparsers(dest="command", required=True)

    baseline = subparsers.add_parser(
        "make-baseline",
        help="Build the bootstrap release bundle from synthetic fixtures",
    )
    baseline.add_argument("--out", type=Path, default=Path("release"))
    baseline.add_argument("--fixtures", type=Path, default=Path("fixtures"))

    args = parser.parse_args(argv)
    if args.command == "make-baseline":
        metadata = build_baseline_bundle(args.out, fixtures_dir=args.fixtures)
        print(json.dumps(metadata, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
