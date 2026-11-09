"""Write a deployment evidence record (no secrets) from GitLab CI variables."""

import argparse
import json
import os
from datetime import UTC, datetime

p = argparse.ArgumentParser()
p.add_argument("--environment", required=True)
p.add_argument("--digest", required=True)
p.add_argument("--output", required=True)
p.add_argument("--rollback", action="store_true")
a = p.parse_args()

record = {
    "environment": a.environment,
    "action": "rollback" if a.rollback else "deploy",
    "image": f"{os.environ.get('CI_REGISTRY_IMAGE', '')}/app@{a.digest}",
    "image_digest": a.digest,
    "source_commit": os.environ.get("CI_COMMIT_SHA"),
    "started_by": os.environ.get("GITLAB_USER_LOGIN"),
    "pipeline_url": os.environ.get("CI_PIPELINE_URL"),
    "job_url": os.environ.get("CI_JOB_URL"),
    "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
}
with open(a.output, "w") as fh:
    json.dump(record, fh, indent=2)
print(json.dumps(record))
