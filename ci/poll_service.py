"""Poll one URL at a fixed interval and summarise the answers (drill evidence K4 and K8).

    python ci/poll_service.py --url http://promiseops.g07-prod.svc.cluster.local/readyz \
        --seconds 120 --output poll.json [--stop-file done]

Prints one line per non-200 answer (time, status or error type) and a JSON summary at the end;
stops early when --stop-file appears. Exit code 0 when every answer was 200.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--output", required=True)
    parser.add_argument("--stop-file")
    args = parser.parse_args()
    started = time.monotonic()
    answers: Counter[str] = Counter()
    first_failure = last_failure = None
    while time.monotonic() - started < args.seconds:
        if args.stop_file and os.path.exists(args.stop_file):
            break
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with urllib.request.urlopen(args.url, timeout=2) as response:
                result = str(response.status)
        except urllib.error.HTTPError as exc:
            result = str(exc.code)
        except Exception as exc:  # noqa: BLE001 - any transport failure is a failed answer
            result = type(exc).__name__
        answers[result] += 1
        if result != "200":
            first_failure = first_failure or now
            last_failure = now
            print(f"{now} {result}", flush=True)
        time.sleep(args.interval)
    summary = {
        "url_path": args.url.split("svc.cluster.local", 1)[-1],
        "seconds": round(time.monotonic() - started, 1),
        "answers": dict(answers),
        "non_200": sum(n for code, n in answers.items() if code != "200"),
        "first_failure": first_failure,
        "last_failure": last_failure,
    }
    with open(args.output, "w") as handle:
        json.dump(summary, handle, indent=2)
    print(json.dumps(summary))
    return 0 if summary["non_200"] == 0 and answers else 1


if __name__ == "__main__":
    raise SystemExit(main())
