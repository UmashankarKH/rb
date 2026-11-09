"""Deterministic rules: the [CODE] steps in the SOP.

These are plain functions so they can be pasted into Runbook code steps as-is.
"""
from __future__ import annotations

from datetime import date

APPROVAL_VALUE_THRESHOLD = 5000.00
CONFIDENCE_THRESHOLD = 0.80
LABOR_TOLERANCE = 1.20  # flag if labor > standard x 1.2


def months_between(start: str, end: str) -> int:
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    months = (e.year - s.year) * 12 + (e.month - s.month)
    if e.day < s.day:
        months -= 1
    return months


# Step 2 (deterministic part): find the failed component
def identify_component(work_order: dict, catalog: list[dict]) -> tuple[dict | None, str]:
    """Return (component, how_matched). Part-number prefix wins over keywords."""
    for part in work_order["parts"]:
        for comp in catalog:
            if any(part["part_number"].startswith(p) for p in comp["part_prefixes"]):
                return comp, "part_prefix"
    notes = work_order["tech_notes"].lower()
    for comp in catalog:
        if any(k in notes for k in comp["keywords"]):
            return comp, "keyword"
    return None, "none"


def primary_part(work_order: dict, component: dict) -> dict | None:
    for part in work_order["parts"]:
        if any(part["part_number"].startswith(p) for p in component["part_prefixes"]):
            return part
    return work_order["parts"][0] if work_order["parts"] else None


# Step 3: warranty coverage
def policy_matches(policy: dict, work_order: dict) -> bool:
    m = policy["match"]
    if "make" in m:
        return work_order.get("make") == m["make"]
    if "engine_make" in m:
        return work_order.get("engine_make") == m["engine_make"]
    if "part_prefix" in m:
        return any(p["part_number"].startswith(m["part_prefix"]) for p in work_order["parts"])
    return False


def check_coverage(work_order: dict, component: dict, policies: list[dict]) -> tuple[dict | None, list[str]]:
    """Return (covering_policy, reasons). reasons explains every policy considered."""
    reasons = []
    for pol in policies:
        if not policy_matches(pol, work_order):
            continue
        pid, comp = pol["policy_id"], component["component"]
        if comp in pol["exclusions"]:
            reasons.append(f"{pid}: {component['name']} is an excluded wear item")
            continue
        if comp not in pol["covered_components"]:
            reasons.append(f"{pid}: {component['name']} is not a covered component")
            continue

        if pol["measure_from"] == "part_install":
            install = work_order.get("original_part_install")
            if not install:
                reasons.append(f"{pid}: no record of when the original part was installed")
                continue
            age = months_between(install["date"], work_order["repair_date"])
            miles = work_order["odometer_mi"] - install["odometer_mi"]
            basis = "since part install"
        else:
            age = months_between(work_order["in_service_date"], work_order["repair_date"])
            miles = work_order["odometer_mi"]
            basis = "since in-service"

        if age > pol["max_months"]:
            reasons.append(f"{pid}: {age} months {basis} exceeds {pol['max_months']}-month limit")
            continue
        if pol["max_miles"] is not None and miles > pol["max_miles"]:
            reasons.append(f"{pid}: {miles:,} miles {basis} exceeds {pol['max_miles']:,} miles limit")
            continue

        reasons.append(f"{pid}: covered ({age} months, {miles:,} miles {basis})")
        return pol, reasons
    if not reasons:
        reasons.append("No warranty policy applies to this unit or part")
    return None, reasons


# Step 5: validation
def collect_claim_fields(work_order: dict, part: dict | None, fault_codes: list[str]) -> dict:
    install = work_order.get("original_part_install") or {}
    return {
        "fault_code": ", ".join(fault_codes) if fault_codes else None,
        "failed_part_serial": (part or {}).get("serial"),
        "failed_part_photo": "failed_part_photo" in work_order.get("attachments", []),
        "original_invoice": install.get("invoice"),
    }


def missing_required_fields(policy: dict, fields: dict) -> list[str]:
    return [f for f in policy["required_fields"] if not fields.get(f)]


def labor_flag(labor_hours: float, component: dict) -> bool:
    # round to avoid float noise (3.0 * 1.2 == 3.5999999999999996)
    return round(labor_hours, 2) > round(component["std_labor_hours"] * LABOR_TOLERANCE, 2)


def claim_value(work_order: dict, policy: dict, labor_hours: float) -> float:
    parts = sum(p["qty"] * p["unit_cost"] for p in work_order["parts"])
    return round(parts + labor_hours * policy["labor_rate"], 2)


def claimable_labor(labor_hours: float, policy: dict) -> float:
    cap = policy.get("labor_hours_cap")
    return min(labor_hours, cap) if cap is not None else labor_hours


def confidence_score(match_method: str, narrative: dict, missing: list[str]) -> float:
    score = 1.0
    if match_method == "keyword":
        score -= 0.25
    if not narrative.get("complaint"):
        score -= 0.15
    if not narrative.get("fault_codes"):
        score -= 0.10
    if not narrative.get("correction"):
        score -= 0.20
    score -= 0.10 * len(missing)
    return round(max(score, 0.0), 2)


# Step 7: approval routing
def approval_reasons(value: float, confidence: float, over_labor: bool) -> list[str]:
    reasons = []
    if value > APPROVAL_VALUE_THRESHOLD:
        reasons.append(f"claim value ${value:,.2f} > ${APPROVAL_VALUE_THRESHOLD:,.0f}")
    if confidence < CONFIDENCE_THRESHOLD:
        reasons.append(f"confidence {confidence} < {CONFIDENCE_THRESHOLD}")
    if over_labor:
        reasons.append("labor over standard")
    return reasons
