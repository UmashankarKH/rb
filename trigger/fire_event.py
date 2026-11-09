"""Simulates Hexagon: sends a signed `work_order.closed` webhook to the trigger.

Usage:
  python trigger/fire_event.py WO-1001              one work order
  python trigger/fire_event.py WO-1001 WO-1004      several
  python trigger/fire_event.py --all                all 8 sample work orders
  python trigger/fire_event.py WO-1001 --event-id evt-1   reuse an id to test duplicates
  python trigger/fire_event.py WO-1001 --bad-signature    test signature rejection
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trigger"))
from webhook_server import HOST, PATH, PORT, sign  # noqa: E402


def fire(wo_id: str, event_id: str | None, bad_signature: bool) -> dict:
    event = {
        "event": "work_order.closed",
        "event_id": event_id or f"evt-{uuid.uuid4().hex[:12]}",
        "wo_id": wo_id,
        "occurred_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "hexagon",
    }
    body = json.dumps(event).encode()
    sig = "sha256=bad" if bad_signature else sign(body)
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}{PATH}", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Hexagon-Signature": sig},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"http": resp.status, **json.loads(resp.read())}
    except urllib.error.HTTPError as e:
        return {"http": e.code, **json.loads(e.read())}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("wo_ids", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--event-id")
    p.add_argument("--bad-signature", action="store_true")
    args = p.parse_args()
    ids = [f"WO-{n}" for n in range(1001, 1009)] if args.all else args.wo_ids
    if not ids:
        p.error("give work order ids or --all")
    for wo_id in ids:
        r = fire(wo_id, args.event_id, args.bad_signature)
        value = f"${r['claim_value']:,.2f}" if r.get("claim_value") else ""
        summary = r.get("status") or r.get("error") or r.get("note") or r.get("ignored")
        print(f"Hexagon -> {wo_id}: HTTP {r['http']}  {summary}  {r.get('claim_number') or ''} {value}".rstrip())


if __name__ == "__main__":
    main()
