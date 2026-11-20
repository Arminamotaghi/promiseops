"""ci/priority_run.py: two Jobs from the priority CronJob template, without a cluster."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("priority_run", ROOT / "ci" / "priority_run.py")
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)

EXAMPLE = json.loads((ROOT / "contracts" / "priority.example.json").read_text(encoding="utf-8"))

CRONJOB = {
    "spec": {
        "suspend": True,
        "jobTemplate": {
            "metadata": {"labels": {"app.kubernetes.io/component": "priority"}},
            "spec": {
                "backoffLimit": 1,
                "template": {"spec": {"containers": [
                    {"name": "priority", "command": ["promiseops"], "args": ["score-priority"]}
                ]}},
            },
        },
    }
}


def output(scored=2):
    orders = [{"rank": 1, "order_id": "a" * 32, "risk_probability": 0.4}][: 1 if scored else 0]
    return {
        "schema_version": "1.0",
        "as_of": "2018-08-01T00:00:00Z",
        "cohort": {"id": "approved:(x,y]", "rule": "first run", "hash": "0" * 64},
        "model_version": "late-risk-logistic_regression.v1.aaf19136",
        "scored_count": scored,
        "k": len(orders),
        "orders": orders,
    }


class FakeCluster:
    """Jobs finish at once; a stored run is returned again for the same as_of (as the CLI)."""

    def __init__(self, results, fail=()):
        self.results = list(results)
        self.fail = set(fail)
        self.jobs = {}
        self.rows = 0

    def kube(self, *args, stdin=None):
        verb = args[2]
        if verb == "get" and args[3] == "cronjob":
            return json.dumps(CRONJOB)
        if verb == "create":
            job = json.loads(stdin)
            name = job["metadata"]["name"]
            failed = name in self.fail
            result = None if failed else self.results.pop(0)
            if result and result["scored_count"]:
                self.rows = max(self.rows, 1)
            self.jobs[name] = {"job": job, "failed": failed, "result": result}
            return ""
        if verb == "get" and args[3] == "job":
            kind = "Failed" if self.jobs[args[4]]["failed"] else "Complete"
            return json.dumps({"status": {"conditions": [{"type": kind, "status": "True"}]}})
        if verb == "logs":
            entry = self.jobs[args[3].split("/", 1)[1]]
            if entry["failed"]:
                return ("Traceback (most recent call last):\n"
                        "  connection to postgresql://user:secret@db.internal/po failed\n"
                        "Priority scoring failed: OperationalError\n")
            return '{"level": "info"}\n' + json.dumps(entry["result"], indent=2) + "\n"
        raise AssertionError(args)


def run(cluster, tmp_path, as_of="2018-08-01T00:00:00Z"):
    return pr.run("g07-staging", "promiseops-priority", as_of, "priority-manual-7", tmp_path,
                  EXAMPLE, cluster.kube, lambda: cluster.rows, timeout=5, poll=0)


def test_build_job_keeps_the_template_and_adds_as_of():
    job = pr.build_job(CRONJOB, "priority-manual-7-1", "2018-08-01T00:00:00Z")
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["args"] == ["score-priority", "--as-of", "2018-08-01T00:00:00Z"]
    assert job["spec"]["backoffLimit"] == 1
    assert job["metadata"]["labels"] == {"app.kubernetes.io/component": "priority"}
    assert CRONJOB["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["args"] == ["score-priority"]
    assert pr.build_job(CRONJOB, "x", None)["spec"]["template"]["spec"]["containers"][0]["args"] == ["score-priority"]


def test_extract_output_skips_log_lines():
    log = '{"level": "info", "msg": "start"}\nwarning\n' + json.dumps(output(), indent=2) + "\n"
    assert pr.extract_output(log) == output()
    with pytest.raises(pr.RunError):
        pr.extract_output('{"level": "info"}\n')


def test_shape_matches_the_contract_example():
    assert pr.shape_problems(output(), EXAMPLE) == []
    broken = output()
    del broken["k"]
    assert pr.shape_problems(broken, EXAMPLE)


def test_two_runs_pass_and_the_second_stores_nothing(tmp_path):
    cluster = FakeCluster([output(), output()])
    assert run(cluster, tmp_path) == 0
    summary = json.loads((tmp_path / "priority-summary.json").read_text())
    assert summary["passed"] and summary["stored_by_first_run"] == 1 and summary["second_run_identical"]
    args = cluster.jobs["priority-manual-7-2"]["job"]["spec"]["template"]["spec"]["containers"][0]["args"]
    assert args[-2:] == ["--as-of", "2018-08-01T00:00:00Z"]


def test_different_second_output_fails(tmp_path):
    second = output()
    second["orders"][0]["risk_probability"] = 0.3
    assert run(FakeCluster([output(), second]), tmp_path) == 1


def test_empty_cohort_fails(tmp_path):
    assert run(FakeCluster([output(scored=0), output(scored=0)]), tmp_path) == 1
    assert "empty cohort" in (tmp_path / "priority-summary.json").read_text()


def test_failed_job_stops_before_the_second_run(tmp_path):
    cluster = FakeCluster([], fail={"priority-manual-7-1"})
    with pytest.raises(pr.RunError):
        run(cluster, tmp_path)
    assert "priority-manual-7-2" not in cluster.jobs


def test_refuses_a_namespace_that_is_not_staging(tmp_path):
    cluster = FakeCluster([output(), output()])
    for namespace in ("g07-prod", "g07-staging-prod", "", "g07-jobs"):
        with pytest.raises(pr.RunError, match="refusing namespace"):
            pr.run(namespace, "promiseops-priority", "2018-08-01T00:00:00Z", "p", tmp_path,
                   EXAMPLE, cluster.kube, lambda: cluster.rows, timeout=5, poll=0)
    assert cluster.jobs == {}


def test_failed_job_prints_only_the_cli_error_and_stores_no_log(tmp_path, capsys):
    cluster = FakeCluster([], fail={"priority-manual-7-1"})
    with pytest.raises(pr.RunError):
        run(cluster, tmp_path)
    printed = capsys.readouterr().out
    assert "Priority scoring failed: OperationalError" in printed
    assert "secret" not in printed and "postgresql://" not in printed
    assert not list(tmp_path.glob("*.log"))


def test_tie_break_and_k_follow_the_contract():
    good = output(scored=12)
    good["k"] = 2
    good["orders"] = [
        {"rank": 1, "order_id": "a" * 32, "risk_probability": 0.4},
        {"rank": 2, "order_id": "b" * 32, "risk_probability": 0.4},
    ]
    assert pr.shape_problems(good, EXAMPLE) == []
    swapped = json.loads(json.dumps(good))
    swapped["orders"][0]["order_id"], swapped["orders"][1]["order_id"] = "b" * 32, "a" * 32
    assert any("order_id ascending" in p for p in pr.shape_problems(swapped, EXAMPLE))
    wrong_k = json.loads(json.dumps(good))
    wrong_k["scored_count"] = 30
    assert any("ceil" in p for p in pr.shape_problems(wrong_k, EXAMPLE))
