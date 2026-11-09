"""The Warranty Claims Agent workflow: SOP steps 1-10, in order.

Every decision is written to `audit`, which is the audit trail Runbook keeps for
each run.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import rules
from .standardize import format_3c, standardize_notes

MAX_TECH_ROUNDS = 2


@dataclass
class Context:
    hexagon: object
    portal: object
    teams: object
    lake: object
    policies: list[dict]
    catalog: list[dict]


@dataclass
class RunResult:
    wo_id: str
    status: str = "STARTED"
    component: str | None = None
    policy_id: str | None = None
    reason: str = ""
    narrative_3c: str = ""
    confidence: float | None = None
    claim_labor_hours: float | None = None
    claim_value: float | None = None
    claim_number: str | None = None
    needed_tech_info: bool = False
    needed_approval: bool = False
    audit: list[dict] = field(default_factory=list)

    def log(self, step: str, detail: str) -> None:
        self.audit.append({"step": step, "detail": detail})


def _finish(ctx: Context, r: RunResult, started: float) -> RunResult:
    # Step 10: write back to Hexagon and record the run
    ctx.hexagon.update_work_order(r.wo_id, {
        "warranty_review_status": r.status,
        "warranty_claim_number": r.claim_number,
        "warranty_claim_value": r.claim_value,
    })
    r.log("10 write back", f"Hexagon updated with status {r.status}")
    record = {k: v for k, v in r.__dict__.items()}
    record["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    ctx.lake.write(record)
    return r


def run_work_order(wo_id: str, ctx: Context) -> RunResult:
    started = time.perf_counter()
    r = RunResult(wo_id=wo_id)

    # Step 1: fetch
    wo = ctx.hexagon.get_work_order(wo_id)
    r.log("1 fetch", f"Loaded {wo_id} ({wo['unit_id']}, {wo['odometer_mi']:,} mi, {len(wo['parts'])} part(s))")

    # Step 2: identify component
    component, how = rules.identify_component(wo, ctx.catalog)
    if component is None:
        r.status, r.reason = "NEEDS_REVIEW", "Failed component not in catalog"
        r.log("2 identify", r.reason + ", sent to warranty admin")
        ctx.teams.notify(wo_id, f"{wo_id}: could not identify a warrantable component, please review.")
        return _finish(ctx, r, started)
    r.component = component["component"]
    r.log("2 identify", f"{component['name']} (matched by {how})")

    # Step 3: coverage
    policy, reasons = rules.check_coverage(wo, component, ctx.policies)
    for reason in reasons:
        r.log("3 coverage", reason)
    if policy is None:
        r.status, r.reason = "NOT_COVERED", "; ".join(reasons)
        return _finish(ctx, r, started)
    r.policy_id = policy["policy_id"]

    # Steps 4-6: standardize, validate, ask technician (loop)
    for round_no in range(MAX_TECH_ROUNDS + 1):
        narrative = standardize_notes(wo["tech_notes"])
        r.narrative_3c = format_3c(narrative)
        r.log("4 standardize", r.narrative_3c.replace("\n", " | "))

        part = rules.primary_part(wo, component)
        fields = rules.collect_claim_fields(wo, part, narrative["fault_codes"])
        missing = rules.missing_required_fields(policy, fields)
        r.confidence = rules.confidence_score(how, narrative, missing)
        r.log("5 validate", f"missing={missing or 'none'}, confidence={r.confidence}")

        if not missing:
            break
        if round_no == MAX_TECH_ROUNDS:
            r.status, r.reason = "NEEDS_TECH_INFO", f"Still missing after {MAX_TECH_ROUNDS} rounds: {missing}"
            return _finish(ctx, r, started)

        r.needed_tech_info = True
        questions = [_question_for(f, component) for f in missing]
        reply = ctx.teams.ask_technician(wo_id, wo["tech_id"], questions)
        r.log("6 ask technician", f"Asked {wo['tech_id']}: {questions}")
        if reply is None:
            r.status, r.reason = "NEEDS_TECH_INFO", f"Waiting on technician for {missing}"
            return _finish(ctx, r, started)
        # Merge the reply: add it to the notes and fill the fields
        wo["tech_notes"] += ". " + reply.get("note", "")
        if reply.get("failed_part_serial") and part is not None:
            part["serial"] = reply["failed_part_serial"]
        r.log("6 ask technician", f"Reply merged: {reply.get('note', '')}")

    # Step 7: approval
    over_labor = rules.labor_flag(wo["labor_hours"], component)
    labor = rules.claimable_labor(wo["labor_hours"], policy)
    if labor != wo["labor_hours"]:
        r.log("7 approval", f"Labor capped by policy at {labor} h")
    value = rules.claim_value(wo, policy, labor)
    needs = rules.approval_reasons(value, r.confidence, over_labor)
    if needs:
        r.needed_approval = True
        card = {"narrative": r.narrative_3c, "policy": r.policy_id, "value": value, "flags": needs}
        decision = ctx.teams.request_approval(wo_id, card)
        r.log("7 approval", f"Sent to admin: {needs}")
        if decision is None:
            r.status, r.reason = "PENDING_APPROVAL", "; ".join(needs)
            r.claim_value = value
            return _finish(ctx, r, started)
        r.log("7 approval", f"Admin decision: {decision['decision']} ({decision.get('comment', '')})")
        if decision["decision"] == "reject":
            r.status, r.reason = "REJECTED_BY_ADMIN", decision.get("comment", "")
            return _finish(ctx, r, started)
        if decision["decision"] == "approve_capped":
            labor = min(labor, component["std_labor_hours"])
            value = rules.claim_value(wo, policy, labor)
            r.log("7 approval", f"Labor capped at standard {labor} h, value now ${value:,.2f}")
    else:
        r.log("7 approval", f"Auto-approved (value ${value:,.2f}, confidence {r.confidence})")
    r.claim_labor_hours, r.claim_value = labor, value

    # Step 8: submit
    claim = {
        "wo_id": wo_id, "vin": wo["vin"], "policy_id": r.policy_id,
        "failure_code": component["failure_code"], "narrative": narrative,
        "labor_hours": labor, "value": value, "fields": fields, "missing_fields": missing,
    }
    resp = ctx.portal.submit(policy["portal"], claim)
    if not resp["ok"]:
        r.status, r.reason = "READY_FOR_MANUAL_SUBMIT", resp["detail"]
        r.log("8 submit", f"{resp['error']}: {resp['detail']}. Claim package sent to admin.")
        ctx.teams.notify(wo_id, f"{wo_id}: portal blocked automation. Claim package ready for one-click manual submit.")
        return _finish(ctx, r, started)
    r.claim_number = resp["claim_number"]
    r.status = "SUBMITTED"
    r.log("8 submit", f"Submitted to {policy['portal']} as {r.claim_number}")

    # Step 9: track
    portal_status = ctx.portal.status(r.claim_number)
    r.status = {"APPROVED": "CLAIM_APPROVED", "REJECTED": "CLAIM_REJECTED"}.get(portal_status, "SUBMITTED")
    r.log("9 track", f"Portal status: {portal_status}")
    return _finish(ctx, r, started)


def _question_for(field_name: str, component: dict) -> str:
    return {
        "fault_code": f"What fault codes were active for the {component['name'].lower()} failure? Reply 'none' if there were none.",
        "failed_part_serial": f"What is the serial number on the failed {component['name'].lower()}?",
        "failed_part_photo": f"Please upload a photo of the failed {component['name'].lower()}.",
        "original_invoice": "What is the invoice number for the original part purchase?",
    }.get(field_name, f"Please provide: {field_name}")
