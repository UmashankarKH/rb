# Warranty Claims Agent (Phase 1, Fleet Maintenance Orchestrator)

When a repair work order closes in Hexagon, this agent checks warranty coverage,
rewrites the technician's notes into a standard 3C claim, asks the technician
for missing details, gets admin approval when needed, submits the claim, and
tracks it.

## What's in this folder

| Path | What it is |
|---|---|
| `runbook/warranty_claims_agent.sop.md` | **The agent's instructions for Runbook.** Paste these into the builder. |
| `agent/rules.py` | Deterministic steps (coverage, validation, approval routing). These become Runbook code steps. |
| `agent/standardize.py` | Local stand-in for the AI step that rewrites notes into 3C. In Runbook the model does this. |
| `agent/integrations.py` | Mock Hexagon, warranty portals, Teams and Azure data lake |
| `agent/workflow.py` | SOP steps 1–10 wired together, with an audit trail |
| `data/` | **Sample data, all fictional**: 8 work orders, 3 warranty policies, component catalog, scripted human replies, expected outcomes |
| `run_test.py` | End-to-end test run |
| `trigger/` | Webhook trigger: receiver (`webhook_server.py`) and Hexagon simulator (`fire_event.py`) |
| `tests/test_agent.py` | Unit tests for rules and for the paths the main run doesn't reach |
| `output/` | Created by the test run: report, data lake records, Teams messages |

## Run the test locally

```bash
python run_test.py
```

```bash
python -m unittest discover -s tests
```

Only the Python standard library is needed (3.10+). The latest run: **8/8 work orders
passed, 16/16 unit tests passed.** Details are in `output/run_report.md`.

## The trigger: Hexagon `work_order.closed` webhook

In production, Hexagon calls a webhook URL when a work order closes, and that
starts the agent. `trigger/` is a working local version of this.

```
Hexagon (work order closed)
   │  POST /webhooks/hexagon   {"event": "work_order.closed", "event_id": "...", "wo_id": "WO-1001"}
   │  header X-Hexagon-Signature: sha256=<HMAC of body with shared secret>
   ▼
trigger/webhook_server.py
   1. Signature valid?          no  → 401, nothing runs
   2. Event type accepted?      no  → 200 "ignored" (e.g. work_order.opened)
   3. Work order exists?        no  → 404
   4. Same event_id seen?       yes → 200 "duplicate" (webhooks get retried)
   5. Run the agent (SOP steps 1–10)
        └ work order already has a claim? → ALREADY_CLAIMED, no second claim
   ▼
JSON response: status, claim number, value, every step taken
```

Start the receiver in one terminal:

```bash
python trigger/webhook_server.py
```

Then act as Hexagon from a second terminal:

```bash
python trigger/fire_event.py --all
```

```bash
python trigger/fire_event.py WO-1001 --event-id evt-demo
```

```bash
python trigger/fire_event.py WO-1002 --bad-signature
```

The server prints each event and every step the agent took. The signing secret
comes from `HEXAGON_WEBHOOK_SECRET` (default `local-test-secret`, for local use only).
The port comes from `TRIGGER_PORT` (default 8787).

In Runbook, this becomes the workflow's webhook trigger. Give Hexagon the Runbook
webhook URL and secret, and keep the same four checks. If Hexagon can't send
webhooks, use the SOP's fallback: poll every 30 minutes for closed work orders
that have no `warranty_review_status`.

### What the 8 test work orders cover

| WO | Scenario | Expected result |
|---|---|---|
| 1001 | EGR cooler, all information present | Auto-submitted, approved |
| 1002 | Turbo, $6.2k claim | Admin approval, then submitted |
| 1003 | Water pump, notes missing fault code and serial | Asks technician, merges the reply, submits |
| 1004 | DPF at 271,900 mi | Not covered (over the mileage limit) |
| 1005 | Alternator under supplier part warranty | Covered, but the portal blocks bots, so it becomes a manual-submit package |
| 1006 | Brake chamber | Not covered (excluded wear item) |
| 1007 | Air compressor, 6 h labor vs 3 h standard | Labor flagged, admin caps labor at standard |
| 1008 | HVAC part not in the catalog | Sent for human review |

## Moving it into Runbook

1. **Create a new agent** in the Runbook builder and paste in `runbook/warranty_claims_agent.sop.md` as the procedure.
2. **Add knowledge sources:** upload `warranty_policies.json` and `component_catalog.json`. Replace them with real data first (see below).
3. **Add code steps:** for SOP steps 3, 5 and 7, paste the matching functions from `agent/rules.py`.
4. **Connect the systems:**
   - Hexagon: API or browser automation, to read closed work orders and write back status (**not yet confirmed with Runbook**)
   - Warranty portals: API where it exists, browser automation otherwise
   - Teams channel `#maintenance-warranty` for technician questions and approvals
   - Azure data lake table `maintenance.warranty_agent_runs`
5. **Test inside Runbook** with the same 8 work orders from `data/work_orders.json`. Check each result against `data/expected_outcomes.json`. This becomes the agent's first evaluation set.
6. **Expand the evaluation set** with 6–12 months of the fleet's real claims, both approved and rejected, and keep improving accuracy before going live.

## Replace before using real data

- `warranty_policies.json`: Apex Engines, Northstar Trucks and Volt Supply Co are **fictional**. Use the fleet's real OEM and supplier warranty terms.
- `component_catalog.json`: failure codes and standard labor times are **placeholders**. Use the OEMs' real code lists and labor-time guides.
- Approval thresholds (`$5,000`, confidence `0.80`, labor `×1.2`) are in `agent/rules.py`. Agree them with the warranty admin.

## Known limitations of the local version

- The note rewriter uses rules, not a language model. It reorders and cleans up the notes but doesn't really understand them. For example, on WO-1007 it puts "bracket bolts seized" under Cause, when that is really a labor justification. Runbook's AI step should handle this better, so test it there.
- The portals are mocks. Real portal behavior (fields, CAPTCHAs, bot blocking) needs to be checked with each OEM.
