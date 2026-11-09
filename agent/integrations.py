"""Mock versions of the systems the agent talks to.

Each class has the same methods the real connector will need, so swapping in
the real Hexagon API, warranty portals, Teams and Azure is a drop-in change.
"""
from __future__ import annotations

import json
from pathlib import Path


class MockHexagon:
    """Stands in for the Hexagon maintenance system."""

    def __init__(self, work_orders: list[dict]):
        self._orders = {wo["wo_id"]: wo for wo in work_orders}
        self.write_backs: dict[str, dict] = {}

    def closed_work_orders(self) -> list[str]:
        return list(self._orders)

    def get_work_order(self, wo_id: str) -> dict:
        return json.loads(json.dumps(self._orders[wo_id]))  # copy, like an API response

    def update_work_order(self, wo_id: str, fields: dict) -> None:
        self.write_backs.setdefault(wo_id, {}).update(fields)


class MockWarrantyPortal:
    """Stands in for OEM and supplier warranty portals.

    `blocks_automation` simulates a vendor that refuses bot access. This is
    a common integration risk, so the agent must fall back to manual submission.
    """

    BLOCKED_PORTALS = {"volt-supply-portal"}

    def __init__(self):
        self.claims: dict[str, dict] = {}
        self._seq = 5000

    def submit(self, portal: str, claim: dict) -> dict:
        if portal in self.BLOCKED_PORTALS:
            return {"ok": False, "error": "AUTOMATION_BLOCKED",
                    "detail": f"{portal} does not allow automated submission"}
        self._seq += 1
        claim_no = f"CLM-{self._seq}"
        self.claims[claim_no] = {"portal": portal, **claim}
        return {"ok": True, "claim_number": claim_no}

    def status(self, claim_number: str) -> str:
        claim = self.claims[claim_number]
        complete = claim["narrative"]["complaint"] and claim["narrative"]["correction"]
        return "APPROVED" if complete and not claim["missing_fields"] else "INFO_REQUESTED"


class MockTeams:
    """Stands in for the Teams channel, replaying scripted human replies."""

    def __init__(self, scripted: dict):
        self._tech = scripted.get("technician_replies", {})
        self._admin = scripted.get("admin_decisions", {})
        self.messages: list[dict] = []

    def ask_technician(self, wo_id: str, tech_id: str, questions: list[str]) -> dict | None:
        self.messages.append({"to": tech_id, "wo_id": wo_id, "type": "question", "text": questions})
        return self._tech.get(wo_id)

    def request_approval(self, wo_id: str, card: dict) -> dict | None:
        self.messages.append({"to": "#maintenance-warranty", "wo_id": wo_id, "type": "approval", "card": card})
        return self._admin.get(wo_id)

    def notify(self, wo_id: str, text: str) -> None:
        self.messages.append({"to": "#maintenance-warranty", "wo_id": wo_id, "type": "info", "text": text})


class MockDataLake:
    """Stands in for the Azure data lake. Writes run records as JSON lines."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def write(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
