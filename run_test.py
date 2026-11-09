"""End-to-end test run of the Warranty Claims Agent against mock systems.

Usage:  python run_test.py
Writes: output/run_report.md, output/datalake_runs.jsonl, output/teams_messages.json
Exit code is 0 only if every work order matches its expected outcome.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from agent.integrations import MockDataLake, MockHexagon, MockTeams, MockWarrantyPortal
from agent.workflow import Context, run_work_order

ROOT = Path(__file__).parent
DATA, OUT = ROOT / "data", ROOT / "output"


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def check(result, exp: dict) -> list[str]:
    errors = []
    if result.status != exp["final_status"]:
        errors.append(f"status {result.status} != {exp['final_status']}")
    for key in ("policy_id", "component", "needed_approval", "needed_tech_info", "claim_labor_hours"):
        if key in exp and getattr(result, key) != exp[key]:
            errors.append(f"{key} {getattr(result, key)!r} != {exp[key]!r}")
    if "reason_contains" in exp and exp["reason_contains"] not in result.reason:
        errors.append(f"reason missing '{exp['reason_contains']}'")
    return errors


def main() -> int:
    OUT.mkdir(exist_ok=True)
    teams = MockTeams(load("simulated_humans.json"))
    ctx = Context(
        hexagon=MockHexagon(load("work_orders.json")["work_orders"]),
        portal=MockWarrantyPortal(),
        teams=teams,
        lake=MockDataLake(OUT / "datalake_runs.jsonl"),
        policies=load("warranty_policies.json")["policies"],
        catalog=load("component_catalog.json")["components"],
    )
    expected = load("expected_outcomes.json")["expected"]

    rows, details, failures = [], [], 0
    for wo_id in ctx.hexagon.closed_work_orders():
        result = run_work_order(wo_id, ctx)
        errors = check(result, expected[wo_id])
        failures += bool(errors)
        mark = "PASS" if not errors else "FAIL"
        value = f"${result.claim_value:,.2f}" if result.claim_value is not None else "-"
        rows.append(f"| {wo_id} | {result.component or '-'} | {result.policy_id or '-'} | {result.status} | {value} | {mark} |")
        print(f"[{mark}] {wo_id:8} {result.status:24} {value:>10}  {'; '.join(errors)}")

        details.append(f"### {wo_id}: {result.status} ({mark})\n")
        if errors:
            details.append(f"**Mismatches:** {'; '.join(errors)}\n")
        if result.narrative_3c:
            details.append("```\n" + result.narrative_3c + "\n```\n")
        details.append("| Step | Detail |\n|---|---|")
        details += [f"| {a['step']} | {a['detail'].replace('|', '/')} |" for a in result.audit]
        details.append("")

    total = len(rows)
    summary = f"**{total - failures}/{total} work orders passed.**"
    report = "\n".join([
        "# Warranty Claims Agent: Test Run Report", "",
        "Local run against mock Hexagon, warranty portals, Teams and data lake.", "",
        summary, "",
        "| Work order | Component | Policy | Final status | Claim value | Result |",
        "|---|---|---|---|---|---|", *rows, "",
        "## Audit trail per work order", "", *details,
    ])
    (OUT / "run_report.md").write_text(report, encoding="utf-8")
    (OUT / "teams_messages.json").write_text(json.dumps(teams.messages, indent=2), encoding="utf-8")
    print(f"\n{total - failures}/{total} passed. Report: {OUT / 'run_report.md'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
