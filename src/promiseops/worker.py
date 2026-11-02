"""Required entry point invoked by the staff's bounded Job dispatcher."""
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["train", "monitor-train"], default="train")
    for name in ("train-end", "validation-end", "as-of"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    # Use PROMISEOPS_DATABASE_URL, MLFLOW_TRACKING_URI, CI_COMMIT_SHA and the
    # supplied immutable incumbent bundle. /tmp is writable; the image is not.
    # mode=train: train/evaluate/register a candidate, preserving the exact bundle.
    # mode=monitor-train: persist a report; train only if the report recommends it.
    # Neither mode deploys production or changes its model alias.
    raise NotImplementedError(f"Implement the queued {args.mode} worker contract")


if __name__ == "__main__":
    main()
