"""Simulates Hexagon calling GitHub: fires the `work_order.closed` webhook trigger.

This sends a repository_dispatch event, which starts the
"Warranty Claims Agent" GitHub Actions workflow for one work order.

Needs a GitHub token that can write to the repo:
  - fine-grained token: repository access to this repo, "Contents: read and write"
  - or a classic token with the "repo" scope
Put it in the GITHUB_TOKEN environment variable. Never commit it.

Usage:
  python trigger/fire_github.py WO-1001
  python trigger/fire_github.py WO-1001 --repo UmashankarKH/rb
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("wo_id")
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "UmashankarKH/rb"))
    args = p.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Set GITHUB_TOKEN first (see the top of this file).", file=sys.stderr)
        return 2

    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    body = json.dumps({
        "event_type": "work_order.closed",
        "client_payload": {
            "wo_id": args.wo_id,
            "event_id": event_id,
            "occurred_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "hexagon",
        },
    }).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{args.repo}/dispatches", data=body, method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "warranty-agent-trigger",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ok = resp.status == 204
    except urllib.error.HTTPError as e:
        print(f"GitHub refused the event: HTTP {e.code} {e.read().decode()[:300]}", file=sys.stderr)
        return 1
    print(f"Sent work_order.closed for {args.wo_id} ({event_id}): {'accepted' if ok else 'unexpected response'}")
    print(f"Watch the run: https://github.com/{args.repo}/actions/workflows/warranty-agent.yml")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
