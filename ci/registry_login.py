"""Write this job's short-lived own-project registry credentials without logging them."""

import json
import os
from pathlib import Path

path = Path.home() / ".docker/config.json"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(
    json.dumps(
        {
            "auths": {
                os.environ["CI_REGISTRY"]: {
                    "username": os.environ["CI_REGISTRY_USER"],
                    "password": os.environ["CI_REGISTRY_PASSWORD"],
                }
            }
        }
    )
)
path.chmod(0o600)
