#!/usr/bin/env python3
"""Verify liveness, readiness and the Prometheus contract after deploy/rollback."""

import argparse
import json
import urllib.request


def fetch(base_url: str, path: str, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"User-Agent": "voice-fairy-tale-deployment-check/1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read().decode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url", help="API origin, for example http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    health_status, health_body = fetch(args.base_url, "/api/health", args.timeout)
    ready_status, ready_body = fetch(args.base_url, "/api/ready", args.timeout)
    metrics_status, metrics_body = fetch(args.base_url, "/api/metrics", args.timeout)
    health = json.loads(health_body)
    ready = json.loads(ready_body)
    if health_status != 200 or health.get("status") != "ok":
        raise SystemExit("liveness check failed")
    if ready_status != 200 or ready.get("status") != "ready":
        raise SystemExit(f"readiness check failed: {ready.get('checks')}")
    if metrics_status != 200 or "voice_http_requests_total" not in metrics_body:
        raise SystemExit("metrics contract check failed")
    print(
        json.dumps(
            {
                "status": "ok",
                "version": health.get("version"),
                "readiness": ready.get("checks"),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
