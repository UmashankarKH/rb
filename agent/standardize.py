"""Step 4: turn free-text technician notes into a 3C narrative.

In Runbook this is the [AI] step, done by the agent's language model using the
SOP rules. This local version is a deterministic stand-in so the workflow can
be tested end to end without a model. It follows the same rule: use only facts
present in the notes, never invent anything.
"""
from __future__ import annotations

import re

ABBREVIATIONS = {
    r"\bdrvr\b": "driver",
    r"\beng\b": "engine",
    r"\bsys\b": "system",
    r"\bext\b": "external",
    r"\bassy\b": "assembly",
    r"\brr\b": "rear",
    r"\btemp\b": "temperature",
    r"\bdiff\b": "differential",
    r"\bregen\b": "regeneration",
    r"\bok\b": "OK",
    r"\s\+\s": " and ",
    r"\bw/": "with ",
}

FAULT_CODE_RE = re.compile(r"\bSPN\s*\d+\s*FMI\s*\d+\b|\b[PBCU][0-9]{4}\b", re.IGNORECASE)

COMPLAINT_HINTS = ("driver", "reports", "complaint", "says", "light on", "blowing", "low power", "not completing", "slow")
CORRECTION_HINTS = ("replaced", "installed", "repaired", "cleaned", "adjusted", "resealed")
VERIFY_HINTS = ("road test", "test drive", "pressure test ok", "output", " ok")


def expand(text: str) -> str:
    for pattern, repl in ABBREVIATIONS.items():
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text


def split_clauses(notes: str) -> list[str]:
    # Split on sentence periods but never inside numbers like 12.1v
    parts = re.split(r"(?<!\d)\.|\.(?!\d)|[;\n]|,\s+", notes)
    return [p.strip() for p in parts if p.strip()]


def sentence(clauses: list[str]) -> str:
    if not clauses:
        return ""
    text = "; ".join(clauses)
    return text[0].upper() + text[1:] + "."


def standardize_notes(notes: str) -> dict:
    fault_codes = sorted({re.sub(r"\s+", " ", c.upper()) for c in FAULT_CODE_RE.findall(notes)})
    clauses = split_clauses(notes)
    complaint, cause, correction, verification = [], [], [], []

    for i, raw in enumerate(clauses):
        low = raw.lower()
        text = expand(raw)
        if any(h in low for h in CORRECTION_HINTS):
            correction.append(text)
        elif correction and any(h in low for h in VERIFY_HINTS):
            verification.append(text)
        elif i == 0 or (not cause and any(h in low for h in COMPLAINT_HINTS)):
            complaint.append(text)
        else:
            cause.append(text)

    return {
        "complaint": sentence(complaint),
        "cause": sentence(cause),
        "correction": sentence(correction + verification),
        "fault_codes": fault_codes,
    }


def format_3c(narrative: dict) -> str:
    lines = [
        f"COMPLAINT: {narrative['complaint'] or '(not stated)'}",
        f"CAUSE: {narrative['cause'] or '(not stated)'}",
        f"CORRECTION: {narrative['correction'] or '(not stated)'}",
    ]
    if narrative["fault_codes"]:
        lines.append(f"FAULT CODES: {', '.join(narrative['fault_codes'])}")
    return "\n".join(lines)
