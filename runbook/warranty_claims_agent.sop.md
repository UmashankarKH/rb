# Warranty Claims Agent: Standard Operating Procedure

> Paste this into the Runbook builder as the agent's instructions. Each numbered
> step maps to one workflow step. Steps marked **[CODE]** should be deterministic
> code steps. Steps marked **[AI]** use the agent's reasoning. Steps marked
> **[HUMAN]** wait for a person in Teams.

## Purpose

When a repair work order closes in Hexagon, decide whether the repair can be
claimed under warranty. If it can, write a clean, standardized claim, get the
missing information or approval it needs, submit it, and track it until it is
paid or rejected.

## Trigger

- Hexagon sends a `work_order.closed` webhook, **or**
- A scheduled poll every 30 minutes finds work orders with status `CLOSED` and no
  `warranty_review_status`.

## Inputs

| Field | Source |
|---|---|
| Work order (unit, VIN, make, in-service date, repair date, odometer, labor hours) | Hexagon |
| Technician notes (free text) | Hexagon |
| Parts used (part number, description, qty, cost, serial number) | Hexagon |
| Attachments (photos, fault-code screenshots) | Hexagon |
| Warranty policies (`warranty_policies.json`) | Knowledge source |
| Component catalog: failure codes, standard labor times (`component_catalog.json`) | Knowledge source |

## Procedure

### Step 1: Fetch work order **[CODE]**
Call Hexagon `GET /work-orders/{id}` with parts and attachments. If the call
fails, retry 3 times, then escalate to `#maintenance-warranty` in Teams.

### Step 2: Identify the failed component **[AI]**
Read the technician notes and the parts list. Pick the single primary failed
component from the component catalog. Match part-number prefixes first, then
keywords in the notes. If no component matches, mark `NEEDS_REVIEW` and send to
the warranty admin.

### Step 3: Check warranty coverage **[CODE]**
For each policy that matches the unit's make (OEM policy) or the part-number
prefix (supplier policy):
- The component must be in the policy's covered components.
- The component must not be in the policy's exclusions (wear items).
- Months in service at repair date must be ≤ `max_months`.
- Odometer must be ≤ `max_miles` (if the policy has a mileage limit).
- For supplier part warranties, measure age and miles from the **original part
  install**, not the truck's in-service date.

If no policy covers the repair: set status `NOT_COVERED`, record the reason, write
it back to Hexagon, and **stop**.

### Step 4: Standardize the technician notes **[AI]**
Rewrite the notes into the 3C format the OEM expects:
- **Complaint:** what the driver or inspection reported.
- **Cause:** the failure that was found and how it was diagnosed (include fault
  codes, for example `SPN 3251 FMI 0`).
- **Correction:** what was replaced or repaired.

Rules:
- Use only facts that are in the notes, parts list or attachments. **Never invent**
  fault codes, measurements or serial numbers.
- Use neutral, technical wording. Remove slang and blame.
- Attach the catalog failure code and the standard labor time.

### Step 5: Validate the claim **[CODE]**
Check that the claim has every field the policy's `required_fields` lists
(for example `fault_code`, `failed_part_serial`, `failed_part_photo`).
Check labor: if `labor_hours` > standard × 1.2, flag `LABOR_OVER_STANDARD`.
Compute a confidence score (see `rules.py`).

### Step 6: Get missing information from the technician **[HUMAN]**
If required fields are missing, message the technician in Teams (or by SMS if
they are not on Teams) with **one specific question per missing field**. Wait up
to 24 hours. Remind once at 8 hours. When they reply, merge the answer and go
back to Step 4. If there is no reply in 24 hours, escalate to the shop manager.

### Step 7: Get approval from the warranty admin **[HUMAN]**
Ask for approval in Teams if **any** of these is true:
- Claim value > $5,000
- Confidence < 0.80
- `LABOR_OVER_STANDARD` flag is set

The approval card shows the 3C narrative, coverage reason, claim value and flags.
The admin can **Approve**, **Approve with labor capped at standard**, or **Reject**
with a reason. Otherwise, auto-approve.

### Step 8: Submit the claim **[CODE / browser]**
Submit to the provider's warranty portal: through its API if available, otherwise
by browser automation using the portal's claim form. Save the claim number.
If the portal blocks automation, send the admin a ready-to-submit claim package
and mark `READY_FOR_MANUAL_SUBMIT`.

### Step 9: Track the outcome **[CODE]**
Check claim status once a day until it is `APPROVED`, `REJECTED` or
`INFO_REQUESTED`. On `INFO_REQUESTED`, go back to Step 6. On `REJECTED`, save
the rejection reason so it can be added to the evaluation set.

### Step 10: Write back and report **[CODE]**
Write `warranty_review_status`, claim number and claim value to the Hexagon work
order. Send the run record (all steps, decisions, timings) to the Azure data
lake table `maintenance.warranty_agent_runs`.

## Guardrails

- Never submit a claim that a person rejected.
- Never invent technical facts. If something is unknown, ask the technician.
- Each work order gets at most one open claim per provider.
- Every decision is written to the audit trail with the rule or reasoning used.

## Final statuses

`NOT_COVERED` · `NEEDS_TECH_INFO` · `PENDING_APPROVAL` · `REJECTED_BY_ADMIN` ·
`SUBMITTED` · `READY_FOR_MANUAL_SUBMIT` · `CLAIM_APPROVED` · `CLAIM_REJECTED` ·
`NEEDS_REVIEW`
