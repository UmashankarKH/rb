"""Unit tests for rules and for the workflow paths the main test run does not reach.

Run:  python -m unittest discover -s tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import rules  # noqa: E402
from agent.integrations import MockDataLake, MockHexagon, MockTeams, MockWarrantyPortal  # noqa: E402
from agent.standardize import standardize_notes  # noqa: E402
from agent.workflow import Context, run_work_order  # noqa: E402


def load(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def make_ctx(scripted):
    tmp = Path(tempfile.mkdtemp()) / "lake.jsonl"
    return Context(
        hexagon=MockHexagon(load("work_orders.json")["work_orders"]),
        portal=MockWarrantyPortal(),
        teams=MockTeams(scripted),
        lake=MockDataLake(tmp),
        policies=load("warranty_policies.json")["policies"],
        catalog=load("component_catalog.json")["components"],
    )


class RuleTests(unittest.TestCase):
    def test_months_between(self):
        self.assertEqual(rules.months_between("2024-09-15", "2026-03-20"), 18)
        self.assertEqual(rules.months_between("2024-09-15", "2026-03-14"), 17)

    def test_labor_flag(self):
        comp = {"std_labor_hours": 3.0}
        self.assertFalse(rules.labor_flag(3.6, comp))
        self.assertTrue(rules.labor_flag(3.7, comp))

    def test_approval_thresholds(self):
        self.assertEqual(rules.approval_reasons(5000.00, 0.8, False), [])
        self.assertEqual(len(rules.approval_reasons(5000.01, 0.79, True)), 3)


class StandardizeTests(unittest.TestCase):
    def test_decimals_are_not_split(self):
        n = standardize_notes("battery light on. output 12.1v at idle. replaced alternator, output 14.2v ok")
        self.assertIn("12.1v", n["cause"])
        self.assertIn("14.2v", n["correction"])

    def test_fault_codes_extracted_not_invented(self):
        self.assertEqual(standardize_notes("code SPN 641 FMI 7 active. replaced turbo")["fault_codes"], ["SPN 641 FMI 7"])
        self.assertEqual(standardize_notes("replaced water pump")["fault_codes"], [])


class WorkflowPathTests(unittest.TestCase):
    def test_admin_reject_is_never_submitted(self):
        ctx = make_ctx({"admin_decisions": {"WO-1002": {"decision": "reject", "comment": "Not a defect"}}})
        r = run_work_order("WO-1002", ctx)
        self.assertEqual(r.status, "REJECTED_BY_ADMIN")
        self.assertIsNone(r.claim_number)
        self.assertEqual(ctx.portal.claims, {})

    def test_no_technician_reply_waits(self):
        r = run_work_order("WO-1003", make_ctx({}))
        self.assertEqual(r.status, "NEEDS_TECH_INFO")
        self.assertIn("fault_code", r.reason)

    def test_no_admin_reply_waits(self):
        r = run_work_order("WO-1002", make_ctx({}))
        self.assertEqual(r.status, "PENDING_APPROVAL")

    def test_write_back_to_hexagon(self):
        ctx = make_ctx({})
        run_work_order("WO-1001", ctx)
        wb = ctx.hexagon.write_backs["WO-1001"]
        self.assertEqual(wb["warranty_review_status"], "CLAIM_APPROVED")
        self.assertTrue(wb["warranty_claim_number"].startswith("CLM-"))


if __name__ == "__main__":
    unittest.main()
