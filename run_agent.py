"""Run the agent for one or more work orders, as a trigger would.

Used by the GitHub Actions workflow (.github/workflows/warranty-agent.yml).

Usage:
  python run_agent.py WO-1001
  python run_agent.py all
  python run_agent.py WO-1001 --event-id evt-123 --source github-dispatch

If GITHUB_STEP_SUMMARY is set (it is inside GitHub Actions), a readable
summary of every run, with its audit trail, is written to the job page.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "trigger"))

from webhook_server import build_context  # noqa: E402

from agent.workflow import run_work_order  # noqa: E402

ALL_IDS = [f"WO-{n}" for n in range(1001, 1009)]


def to_markdown(results, source: str, event_id: str | None) -> str:
    lines = [
        "## Warranty Claims Agent run",
        "",
        f"**Trigger:** `{source}`" + (f" · **event:** `{event_id}`" if event_id else ""),
        "",
        "| Work order | Status | Policy | Claim | Value |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        value = f"${r.claim_value:,.2f}" if r.claim_value is not None else "-"
        lines.append(f"| {r.wo_id} | **{r.status}** | {r.policy_id or '-'} | {r.claim_number or '-'} | {value} |")
    for r in results:
        lines += ["", f"<details><summary>{r.wo_id}: {r.status}</summary>", ""]
        if r.narrative_3c:
            lines += ["```", r.narrative_3c, "```", ""]
        lines += [f"- **{a['step']}**: {a['detail']}" for a in r.audit]
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("wo_id", help="a work order id such as WO-1001, or 'all'")
    p.add_argument("--event-id")
    p.add_argument("--source", default="manual")
    args = p.parse_args()

    wo_ids = ALL_IDS if args.wo_id.strip().lower() == "all" else [args.wo_id.strip().upper()]
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    ctx = build_context(out)

    results = []
    for wo_id in wo_ids:
        try:
            ctx.hexagon.get_work_order(wo_id)
        except KeyError:
            print(f"::error::Work order {wo_id} not found")
            return 1
        r = run_work_order(wo_id, ctx)
        results.append(r)
        value = f"${r.claim_value:,.2f}" if r.claim_value is not None else ""
        print(f"{wo_id:8} {r.status:24} {r.claim_number or '':10} {value}")
        for a in r.audit:
            print(f"    {a['step']}: {a['detail']}")

    md = to_markdown(results, args.source, args.event_id)
    (out / "agent_run.md").write_text(md, encoding="utf-8")
    (out / "agent_run.json").write_text(
        json.dumps([r.__dict__ for r in results], indent=2), encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(md)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # One notice annotation per work order. Callers such as the Runbook workflow read
        # these through GitHub's check-run annotations API.
        for r in results:
            print(f"::notice title=WARRANTY_RESULT::{_annotation(r, args.event_id)}")
    return 0


def _annotation(r, event_id: str | None) -> str:
    payload = json.dumps({
        "event_id": event_id, "wo_id": r.wo_id, "status": r.status,
        "component": r.component, "policy_id": r.policy_id,
        "claim_number": r.claim_number, "claim_value": r.claim_value,
        "reason": r.reason, "narrative_3c": r.narrative_3c,
        "needed_approval": r.needed_approval, "needed_tech_info": r.needed_tech_info,
    }, separators=(",", ":"))
    # GitHub workflow-command escaping for the message part
    return payload.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


if __name__ == "__main__":
    sys.exit(main())
