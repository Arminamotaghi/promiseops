#!/usr/bin/env python3
"""Public contract smoke check against a real deployed service; API key comes from the environment."""

import argparse
import os
import uuid

import httpx

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--order-id", required=True)
    a = p.parse_args()
    with httpx.Client(base_url=a.url, timeout=15) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        body = {"order_id": a.order_id, "request_id": uuid.uuid4().hex}
        assert client.post("/predict", json=body).status_code == 401
        headers = {"X-API-Key": os.environ["PROMISEOPS_API_KEY"]}
        first = client.post("/predict", json=body, headers=headers)
        assert first.status_code == 200, first.status_code
        result = first.json()
        assert 0 <= result["risk_probability"] <= 1 and result["model_version"]
        assert client.post("/predict", json=body, headers=headers).json() == result
        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers=headers).status_code == 200
    print("Live API contract passed.")
