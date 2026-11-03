#!/usr/bin/env python3
"""Open-loop, bounded-concurrency load test. Missed slots and client errors count as failures."""

import argparse
import asyncio
import json
import math
import os
import time
import uuid
from collections import Counter
from pathlib import Path

import httpx


async def run(url, order_ids, rps=10, seconds=180, max_concurrency=32, api_key=None):
    api_key = api_key or os.environ["PROMISEOPS_API_KEY"]
    latency, codes, dispatch, service, errors = [], [], [], [], Counter()
    semaphore = asyncio.Semaphore(max_concurrency)
    async with httpx.AsyncClient(timeout=5, limits=httpx.Limits(max_connections=max_concurrency)) as client:
        deadline = time.monotonic() + 60
        while True:
            try:
                ready = await client.get(url.rstrip("/") + "/readyz")
                if ready.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Service did not become ready; no load acceptance result produced")
            await asyncio.sleep(1)
        for _ in range(5):
            warmup = await client.post(
                url.rstrip("/") + "/predict",
                json={"order_id": order_ids[0], "request_id": uuid.uuid4().hex},
                headers={"X-API-Key": api_key},
            )
            warmup.raise_for_status()
        started = time.monotonic()

        async def request(index):
            due = started + index / rps
            await asyncio.sleep(max(0, due - time.monotonic()))
            begin = time.monotonic()
            dispatch.append(begin - due)
            async with semaphore:
                try:
                    response = await client.post(
                        url.rstrip("/") + "/predict",
                        json={"order_id": order_ids[index % len(order_ids)], "request_id": uuid.uuid4().hex},
                        headers={"X-API-Key": api_key},
                    )
                    status = response.status_code
                except httpx.HTTPError as exc:
                    status = 0
                    errors[type(exc).__name__] += 1
            finished = time.monotonic()
            service.append(finished - begin)
            latency.append(finished - due)
            codes.append(status)

        await asyncio.gather(*(request(i) for i in range(rps * seconds)))
    p95 = sorted(latency)[math.ceil(0.95 * len(latency)) - 1]
    failures = sum(c == 0 or c >= 500 for c in codes) / len(codes)
    bad_contract = sum(c != 200 for c in codes)
    return {
        "requests": len(codes),
        "target_rps": rps,
        "seconds": seconds,
        "p95_seconds": p95,
        "dispatch_delay_p95_seconds": sorted(dispatch)[math.ceil(0.95 * len(dispatch)) - 1],
        "request_duration_p95_seconds": sorted(service)[math.ceil(0.95 * len(service)) - 1],
        "http_status_counts": dict(Counter(codes)),
        "transport_errors": dict(errors),
        "server_or_transport_error_fraction": failures,
        "non_200": bad_contract,
        "warmup_requests": 5,
        "passed": p95 <= 0.5 and failures < 0.01 and bad_contract == 0,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--order-ids", type=Path, required=True, help="JSON list of released order IDs")
    p.add_argument("--rps", type=int, default=10)
    p.add_argument("--seconds", type=int, default=180)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if not 1 <= a.rps <= 100 or not 1 <= a.seconds <= 600:
        raise SystemExit("Use 1..100 RPS for 1..600 seconds.")
    ids = json.loads(a.order_ids.read_text())
    if not ids:
        raise SystemExit("At least one available order ID required.")
    report = asyncio.run(run(a.url, ids, a.rps, a.seconds))
    a.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    raise SystemExit(0 if report["passed"] else 1)
