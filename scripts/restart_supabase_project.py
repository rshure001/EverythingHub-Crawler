#!/usr/bin/env python3
"""Safely restart the configured Supabase project and report service health.

Requires SUPABASE_ACCESS_TOKEN scoped to this project with Project Settings
read/write permission. Never prints or persists the token.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

PROJECT_REF = os.environ.get("SUPABASE_PROJECT_REF", "vdceayvfgkwpgwixrhwi")
TOKEN = os.environ.get("SUPABASE_ACCESS_TOKEN")
BASE = "https://api.supabase.com/v1"


def request(path, method="GET"):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1200]
        raise RuntimeError(f"Supabase API returned HTTP {exc.code}: {detail}") from exc


def main():
    if not TOKEN:
        print("Missing SUPABASE_ACCESS_TOKEN secret.", file=sys.stderr)
        return 2
    if os.environ.get("CONFIRM_RESTART") != "YES":
        print("Safety stop: set CONFIRM_RESTART=YES to authorize a production restart.")
        return 2

    print(f"Requesting restart for Supabase project {PROJECT_REF}...")
    status, result = request(f"/projects/{PROJECT_REF}/restart", method="POST")
    print(f"Restart request accepted (HTTP {status}).")
    if result:
        print(json.dumps(result, indent=2)[:2000])

    print("Checking project health for up to 10 minutes...")
    deadline = time.time() + 600
    last = None
    while time.time() < deadline:
        try:
            health_status, health = request(f"/projects/{PROJECT_REF}/health")
            last = health
            rendered = json.dumps(health, sort_keys=True)
            print(f"Health endpoint HTTP {health_status}: {rendered[:2500]}")
            if "ACTIVE_HEALTHY" in rendered:
                print("Supabase reports ACTIVE_HEALTHY.")
                return 0
        except Exception as exc:
            print(f"Health check not ready: {exc}")
        time.sleep(20)

    print("Restart was requested, but health did not confirm ACTIVE_HEALTHY within 10 minutes.")
    if last is not None:
        print(json.dumps(last, indent=2)[:3000])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
