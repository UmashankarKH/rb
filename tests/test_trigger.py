"""Tests for the webhook trigger: signature check, event filtering, duplicates.

Run:  python -m unittest discover -s tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trigger"))

from webhook_server import TriggerHandler, build_context, sign  # noqa: E402


def event(**overrides):
    e = {"event": "work_order.closed", "event_id": "evt-1", "wo_id": "WO-1001"}
    e.update(overrides)
    return json.dumps(e).encode()


class TriggerTests(unittest.TestCase):
    def setUp(self):
        self.t = TriggerHandler(build_context(Path(tempfile.mkdtemp())))

    def test_valid_event_runs_agent(self):
        body = event()
        code, r = self.t.handle(body, sign(body))
        self.assertEqual(code, 200)
        self.assertEqual(r["status"], "CLAIM_APPROVED")
        self.assertTrue(r["claim_number"].startswith("CLM-"))

    def test_bad_or_missing_signature_rejected(self):
        body = event()
        self.assertEqual(self.t.handle(body, "sha256=bad")[0], 401)
        self.assertEqual(self.t.handle(body, None)[0], 401)

    def test_tampered_body_rejected(self):
        sig = sign(event())
        self.assertEqual(self.t.handle(event(wo_id="WO-1002"), sig)[0], 401)

    def test_other_event_types_ignored(self):
        body = event(event="work_order.opened")
        code, r = self.t.handle(body, sign(body))
        self.assertEqual(code, 200)
        self.assertIn("ignored", r)

    def test_duplicate_event_not_run_twice(self):
        body = event()
        self.t.handle(body, sign(body))
        code, r = self.t.handle(body, sign(body))
        self.assertEqual(code, 200)
        self.assertIn("duplicate", r)
        self.assertEqual(len(self.t.ctx.portal.claims), 1)

    def test_new_event_for_already_claimed_work_order(self):
        first = event(event_id="evt-1")
        second = event(event_id="evt-2")  # e.g. work order reopened and closed again
        self.t.handle(first, sign(first))
        code, r = self.t.handle(second, sign(second))
        self.assertEqual(code, 200)
        self.assertEqual(r["status"], "ALREADY_CLAIMED")
        self.assertEqual(len(self.t.ctx.portal.claims), 1)

    def test_unknown_work_order(self):
        body = event(wo_id="WO-9999")
        self.assertEqual(self.t.handle(body, sign(body))[0], 404)


if __name__ == "__main__":
    unittest.main()
