"""Run the batch priority scoring twice with the same as_of and check the result.

The CronJob ``<release>-priority`` is suspended in staging and production and the team has
no ``kubectl`` access, so this is the only way the scoring runs on the cluster. It is used by
the manual CI job ``priority-run-staging`` (evidence A4).

It starts two Jobs from the CronJob's own job template, so image, settings, secrets and
limits are exactly the CronJob's; only the arguments get ``--as-of``. The second Job starts
after the first has finished. Checks:

* the first run stores at most one ``priority_runs`` row;
* the second run returns the same output and stores nothing;
* the output has the shape of ``contracts/priority.example.json``.

An empty cohort stores nothing and is reported as a failure: choose another ``--as-of``.
It prints the two outputs, the row counts and the Job names; never a secret or the database
URL. The database URL is read from ``PROMISEOPS_DATABASE_URL`` only to count rows.

    python ci/priority_run.py --namespace g07-staging --cronjob promiseops-priority \\
        --as-of 2018-08-01T00:00:00Z --name-prefix priority-manual-123 --out drill
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

Kubectl = Callable[..., str]

_PREFIXES = ("postgresql+psycopg://", "postgresql+psycopg2://", "postgres://")


class RunError(RuntimeError):
    pass


def kubectl(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(["kubectl", *args], input=stdin, capture_output=True, text=True)
    if result.returncode != 0:
        verb = " ".join(a for a in args if not a.startswith("-"))[:60]
        raise RunError(f"kubectl {verb} failed: {result.stderr.strip()[:300]}")
    return result.stdout


def count_priority_runs(url: str) -> int:
    import psycopg

    for prefix in _PREFIXES:
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix) :]
    try:
        with psycopg.connect(url, connect_timeout=15) as conn:
            return conn.execute("SELECT count(*) FROM priority_runs").fetchone()[0]
    except psycopg.Error as exc:
        # The type only: a connection message can name the host and the user.
        raise RunError(f"cannot count priority_runs: {type(exc).__name__}") from None


def build_job(cronjob: dict, name: str, as_of: str | None) -> dict:
    """A Job from the CronJob's template; only the name and ``--as-of`` differ."""
    template = copy.deepcopy(cronjob["spec"]["jobTemplate"])
    spec = template["spec"]
    containers = spec["template"]["spec"]["containers"]
    if len(containers) != 1:
        raise RunError("the priority job template must have exactly one container")
    args = list(containers[0].get("args") or [])
    if "--as-of" in args:
        raise RunError("the job template already sets --as-of")
    if as_of:
        args += ["--as-of", as_of]
    containers[0]["args"] = args
    metadata = template.get("metadata") or {}
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "labels": dict(metadata.get("labels") or {}),
            "annotations": {"cronjob.kubernetes.io/instantiate": "manual"},
        },
        "spec": spec,
    }


def extract_output(log: str) -> dict:
    """The last JSON object in the log that looks like a priority output."""
    decoder = json.JSONDecoder()
    found = None
    for index, char in enumerate(log):
        if char != "{" or (index and log[index - 1] != "\n"):
            continue
        try:
            value, _ = decoder.raw_decode(log[index:])
        except ValueError:
            continue
        if isinstance(value, dict) and "cohort" in value and "scored_count" in value:
            found = value
    if found is None:
        raise RunError("no priority output in the job log")
    return found


def shape_problems(output: dict, example: dict) -> list[str]:
    problems = []
    if set(output) != set(example):
        problems.append(f"top-level keys {sorted(output)} differ from {sorted(example)}")
    if set(output.get("cohort") or {}) != set(example["cohort"]):
        problems.append("cohort keys differ from the contract")
    order_keys = set(example["orders"][0])
    for order in output.get("orders") or []:
        if set(order) != order_keys:
            problems.append(f"order keys {sorted(order)} differ from {sorted(order_keys)}")
            break
    orders = output.get("orders") or []
    if output.get("k") != len(orders):
        problems.append(f"k is {output.get('k')} but {len(orders)} orders are listed")
    ranks = [order.get("rank") for order in orders]
    if ranks != list(range(1, len(orders) + 1)):
        problems.append("ranks are not 1..k")
    risks = [order.get("risk_probability") for order in orders]
    if any(not isinstance(r, (int, float)) or not 0 <= r <= 1 for r in risks):
        problems.append("a risk_probability is outside [0, 1]")
    elif risks != sorted(risks, reverse=True):
        problems.append("orders are not sorted by risk")
    return problems


def wait_for(kube: Kubectl, namespace: str, name: str, timeout: float, poll: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        status = json.loads(kube("-n", namespace, "get", "job", name, "-o", "json")).get("status") or {}
        for condition in status.get("conditions") or []:
            if condition.get("status") == "True" and condition.get("type") == "Complete":
                return True
            if condition.get("status") == "True" and condition.get("type") == "Failed":
                return False
        if time.monotonic() > deadline:
            raise RunError(f"job {name} did not finish within {int(timeout)} s")
        time.sleep(poll)


def run_once(kube: Kubectl, namespace: str, cronjob: dict, name: str, as_of: str | None,
             out: Path, timeout: float, poll: float) -> dict:
    job = build_job(cronjob, name, as_of)
    kube("-n", namespace, "create", "-f", "-", stdin=json.dumps(job))
    print(f"{time.strftime('%H:%M:%S', time.gmtime())} started job {name}", flush=True)
    succeeded = wait_for(kube, namespace, name, timeout, poll)
    log = kube("-n", namespace, "logs", f"job/{name}", "--tail=-1")
    (out / f"{name}.log").write_text(log, encoding="utf-8")
    print(f"{time.strftime('%H:%M:%S', time.gmtime())} job {name} {'complete' if succeeded else 'FAILED'}", flush=True)
    if not succeeded:
        print(log[-3000:])
        raise RunError(f"job {name} failed")
    return extract_output(log)


def run(namespace: str, cronjob_name: str, as_of: str | None, prefix: str, out: Path,
        example: dict, kube: Kubectl, count: Callable[[], int],
        timeout: float = 960, poll: float = 5) -> int:
    out.mkdir(parents=True, exist_ok=True)
    cronjob = json.loads(kube("-n", namespace, "get", "cronjob", cronjob_name, "-o", "json"))
    print(f"cronjob {cronjob_name}: suspend={cronjob['spec'].get('suspend')}")

    before = count()
    first = run_once(kube, namespace, cronjob, f"{prefix}-1", as_of, out, timeout, poll)
    after_first = count()
    second = run_once(kube, namespace, cronjob, f"{prefix}-2", as_of, out, timeout, poll)
    after_second = count()

    (out / "priority-output.json").write_text(json.dumps(first, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(first, indent=2, sort_keys=True))

    problems = shape_problems(first, example)
    if first.get("scored_count", 0) == 0:
        problems.append("empty cohort: nothing was scored or stored; run again with another --as-of")
    if after_first - before not in (0, 1):
        problems.append(f"first run changed priority_runs by {after_first - before}")
    if second != first:
        problems.append("second run returned a different output")
    if after_second != after_first:
        problems.append(f"second run changed priority_runs by {after_second - after_first}")

    summary = {
        "namespace": namespace,
        "as_of": first.get("as_of"),
        "model_version": first.get("model_version"),
        "cohort_id": (first.get("cohort") or {}).get("id"),
        "scored_count": first.get("scored_count"),
        "k": first.get("k"),
        "priority_runs": {"before": before, "after_first": after_first, "after_second": after_second},
        "stored_by_first_run": after_first - before,
        "second_run_identical": second == first,
        "problems": problems,
        "passed": not problems,
    }
    if after_first == before and not problems:
        summary["note"] = "this as_of was already stored before this job; both runs returned it"
    (out / "priority-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--cronjob", required=True)
    ap.add_argument("--as-of", default="", help="company clock; empty = the CLI default (latest released order)")
    ap.add_argument("--name-prefix", required=True)
    ap.add_argument("--out", type=Path, default=Path("drill"))
    ap.add_argument("--example", type=Path, default=Path("contracts/priority.example.json"))
    ap.add_argument("--timeout", type=float, default=960)
    args = ap.parse_args(argv)

    url = os.environ.get("PROMISEOPS_DATABASE_URL", "")
    if not url:
        print("PROMISEOPS_DATABASE_URL is not set", file=sys.stderr)
        return 2
    example = json.loads(args.example.read_text(encoding="utf-8"))
    try:
        return run(args.namespace, args.cronjob, args.as_of or None, args.name_prefix, args.out,
                   example, kubectl, lambda: count_priority_runs(url), timeout=args.timeout)
    except RunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
