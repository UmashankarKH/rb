"""Trigger: receives Hexagon `work_order.closed` webhooks and starts the agent.

This is SOP "Trigger" made real. In production Hexagon calls this URL when a
work order closes. In Runbook, the same thing is a webhook trigger on the
workflow.

Run:   python trigger/webhook_server.py            (listens on http://127.0.0.1:8787)
Fire:  python trigger/fire_event.py WO-1001         (in a second terminal)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.integrations import MockDataLake, MockHexagon, MockTeams, MockWarrantyPortal  # noqa: E402
from agent.workflow import Context, run_work_order  # noqa: E402

HOST, PORT = "127.0.0.1", int(os.environ.get("TRIGGER_PORT", "8787"))
PATH = "/webhooks/hexagon"
# Shared secret Hexagon signs each webhook with. Sample value for local testing only.
SECRET = os.environ.get("HEXAGON_WEBHOOK_SECRET", "local-test-secret").encode()
ACCEPTED_EVENTS = {"work_order.closed"}


def sign(body: bytes, secret: bytes = SECRET) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def build_context(out_dir: Path) -> Context:
    def load(name):
        return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
    return Context(
        hexagon=MockHexagon(load("work_orders.json")["work_orders"]),
        portal=MockWarrantyPortal(),
        teams=MockTeams(load("simulated_humans.json")),
        lake=MockDataLake(out_dir / "trigger_runs.jsonl"),
        policies=load("warranty_policies.json")["policies"],
        catalog=load("component_catalog.json")["components"],
    )


class TriggerHandler:
    """The trigger logic, kept apart from HTTP so it can be unit-tested."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.processed: dict[str, str] = {}  # event_id -> wo_id (webhooks get retried)
        self._lock = Lock()

    def handle(self, body: bytes, signature: str | None) -> tuple[int, dict]:
        if not signature or not hmac.compare_digest(signature, sign(body)):
            return 401, {"error": "invalid signature"}
        try:
            event = json.loads(body)
        except json.JSONDecodeError:
            return 400, {"error": "body is not JSON"}

        etype, wo_id, event_id = event.get("event"), event.get("wo_id"), event.get("event_id")
        if etype not in ACCEPTED_EVENTS:
            return 200, {"ignored": f"event type {etype!r} does not start this workflow"}
        if not wo_id or not event_id:
            return 400, {"error": "event_id and wo_id are required"}

        try:
            self.ctx.hexagon.get_work_order(wo_id)
        except KeyError:
            return 404, {"error": f"work order {wo_id} not found in Hexagon"}
        with self._lock:
            if event_id in self.processed:
                return 200, {"duplicate": event_id, "wo_id": wo_id, "note": "already processed, not run again"}
            self.processed[event_id] = wo_id

        result = run_work_order(wo_id, self.ctx)
        return 200, {
            "event_id": event_id, "wo_id": wo_id, "status": result.status,
            "policy_id": result.policy_id, "claim_number": result.claim_number,
            "claim_value": result.claim_value, "reason": result.reason,
            "steps": [f"{a['step']}: {a['detail']}" for a in result.audit],
        }


def make_http_handler(trigger: TriggerHandler):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != PATH:
                return self._send(404, {"error": "unknown path"})
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            code, payload = trigger.handle(body, self.headers.get("X-Hexagon-Signature"))
            stamp = datetime.now().strftime("%H:%M:%S")
            label = payload.get("status") or next(iter(payload))
            print(f"[{stamp}] {code} {payload.get('wo_id', '-'):8} -> {label}", flush=True)
            for step in payload.get("steps", []):
                print(f"           {step}", flush=True)
            self._send(code, payload)

        def do_GET(self):
            self._send(200, {"ok": True, "listening": PATH})

        def _send(self, code, payload):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):  # keep the console to our own lines
            pass

    return Handler


def main():
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    trigger = TriggerHandler(build_context(out))
    server = ThreadingHTTPServer((HOST, PORT), make_http_handler(trigger))
    print(f"Warranty agent trigger listening on http://{HOST}:{PORT}{PATH}", flush=True)
    print("Waiting for Hexagon work_order.closed events...", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
