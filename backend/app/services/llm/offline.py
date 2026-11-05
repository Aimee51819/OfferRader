"""Non-network LLM stand-ins.

* ``ReplayBackend`` returns pre-recorded JSON keyed by subject. Used by tests and
  the synthetic demo so the pipeline is deterministic without Ollama.
* ``RulesBackend`` is a transparent regex baseline. It exists so the evaluation
  harness can report honest numbers for a non-LLM method next to Qwen; it is
  NOT used for live mail (live mail always waits for the local model).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ...domain.normalize import (
    clean_company_display,
    detect_assessment_provider,
    extract_requisition_ids,
    is_shared_sender_domain,
    domain_of,
)
from .ollama import LLMBadResponse, LLMUnavailable


def _subject_of(user: str) -> str:
    m = re.search(r"^- Subject: (.*)$", user, re.MULTILINE)
    return (m.group(1) if m else "").strip()


def _body_of(user: str) -> str:
    m = re.search(r"<email>\n(.*)\n</email>", user, re.DOTALL)
    return m.group(1) if m else ""


def _sender_of(user: str) -> str:
    m = re.search(r"^- From: (.*)$", user, re.MULTILINE)
    return (m.group(1) if m else "").strip()


@dataclass
class ReplayBackend:
    responses: dict[str, str | dict]
    name: str = "replay"
    parameters: dict = field(default_factory=lambda: {"mode": "replay"})
    unavailable: bool = False
    calls: int = 0

    def chat_json(self, system: str, user: str, schema: dict) -> str:
        self.calls += 1
        if self.unavailable:
            raise LLMUnavailable("Ollama is not reachable (simulated)")
        subject = _subject_of(user)
        if subject not in self.responses:
            raise LLMBadResponse(f"no replay for subject {subject!r}")
        value = self.responses[subject]
        return value if isinstance(value, str) else json.dumps(value)


_EVENT_RULES: list[tuple[str, re.Pattern]] = [
    ("job_alert", re.compile(r"job alert|jobs? (?:you may|for you|recommended)|recommended jobs|new jobs? (?:matching|posted)|\d+\+? new jobs", re.I)),
    ("offer", re.compile(r"pleased to (?:extend|offer)|offer letter|excited to offer you", re.I)),
    ("rejection", re.compile(r"not (?:be )?mov(?:e|ing) forward|decided to (?:pursue|move forward with) other|unfortunately|no longer under consideration|not been selected", re.I)),
    ("withdrawal", re.compile(r"(?:has|have) been withdrawn|withdr[ae]w your application|position (?:has been )?(?:closed|cancelled)", re.I)),
    ("interview_cancelled", re.compile(r"interview (?:has been )?cancel", re.I)),
    ("interview_rescheduled", re.compile(r"interview (?:has been )?(?:re-?scheduled|moved)", re.I)),
    ("assessment_completed", re.compile(r"(?:received|completed) your (?:online )?assessment|assessment (?:has been )?(?:received|completed)", re.I)),
    ("assessment_reminder", re.compile(r"reminder.{0,60}assessment|assessment.{0,40}reminder|still (?:need|have) to complete", re.I)),
    ("assessment_invitation", re.compile(r"(?:online|coding|technical) assessment|complete (?:the|your|an|a) (?:\w+ )?(?:assessment|challenge|test)|hackerrank|codesignal|hirevue|codility", re.I)),
    ("interview_invitation", re.compile(r"invite you to (?:an? )?(?:\w+ )?interview|schedule (?:an? |your )?interview|interview invitation", re.I)),
    ("screening", re.compile(r"phone screen|recruiter (?:call|screen)|introductory call|quick call", re.I)),
    ("application_confirmation", re.compile(r"thank you for (?:your )?appl|thanks for appl|received your application|application (?:has been )?(?:received|submitted)", re.I)),
    ("portal_update", re.compile(r"status (?:of your application )?(?:has )?(?:changed|been updated)|application status", re.I)),
]
_ROLE_PATTERNS = [
    re.compile(r"(?:for|to) the (?P<t>[A-Z][\w&/,\- ]{3,80}?) (?:position|role|opening|internship)\b"),
    re.compile(r"(?:position|role) of (?P<t>[A-Z][\w&/,\- ]{3,80}?)(?:[.,]| at | with |\n)"),
    re.compile(r"application for (?:the )?(?P<t>[A-Z][\w&/,\- ]{3,80}?)(?: \(|[.,]| at | with |\n| -)"),
]
_COMPANY_PATTERNS = [
    re.compile(r"(?:interest in|applying (?:to|at)|career(?:s)? at|team at|position at|role at|opportunit(?:y|ies) at|join) (?P<c>[A-Z][\w&.\- ]{1,40}?)(?:[.,!]|\s+(?:for|and|as|we|is|in)\b|\n)"),
]
_DEADLINE = re.compile(
    r"(within\s+\w+(?:-\w+)?\s+(?:business\s+)?(?:hours?|days?)(?:\s+of\s+[\w ]{3,40}?)?(?=[.,;\n]|$)"
    r"|(?:by|no later than|before|due(?: by| on)?)\s+(?:\w+day,?\s+)?[A-Z][a-z]+\.? \d{1,2}(?:st|nd|rd|th)?(?:,? \d{4})?(?:(?: at)? \d{1,2}(?::\d{2})?\s*(?:[AaPp]\.?[Mm]\.?)(?:\s*\(?[A-Z]{2,4}\)?)?)?"
    r"|(?:by|no later than|before)\s+\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:(?: at)? \d{1,2}(?::\d{2})?\s*(?:[AaPp][Mm])(?:\s*[A-Z]{2,4})?)?)",
    re.I,
)


@dataclass
class RulesBackend:
    name: str = "rules-baseline"
    parameters: dict = field(default_factory=lambda: {"mode": "regex"})

    def chat_json(self, system: str, user: str, schema: dict) -> str:
        subject, body, sender = _subject_of(user), _body_of(user), _sender_of(user)
        text = f"{subject}\n{body}"
        events = []
        for etype, pat in _EVENT_RULES:
            m = pat.search(text)
            if not m:
                continue
            if etype == "job_alert":
                events = [{"type": "job_alert", "evidence_quotes": [m.group(0)], "confidence": 0.7}]
                break
            if any(e["type"] == etype for e in events):
                continue
            ev: dict = {"type": etype, "evidence_quotes": [m.group(0)], "confidence": 0.6}
            if etype.startswith("assessment"):
                ev["assessment_type"] = "OA"
                ev["assessment_provider"] = detect_assessment_provider(text)
                d = _DEADLINE.search(text)
                if d:
                    ev["deadline"] = {"raw_expression": d.group(0).strip(), "kind": "unknown", "confidence": 0.6}
            events.append(ev)
            if etype in {"rejection", "offer"}:
                break
        company = None
        m = re.match(r'^"?([^"<]+?)"?\s*<', sender)
        dom = domain_of(sender)
        for pat in _COMPANY_PATTERNS:
            cm = pat.search(body)
            if cm:
                company = cm.group("c").strip()
                break
        if company is None and m:
            name = clean_company_display(m.group(1))
            if name and not re.search(r"workday|greenhouse|lever|icims|hackerrank|codesignal|hirevue|linkedin|indeed|handshake", name, re.I):
                company = name
        if company is None and dom and not is_shared_sender_domain(dom):
            company = dom.split(".")[-2].capitalize() if dom.count(".") >= 1 else None
        title = None
        for pat in _ROLE_PATTERNS:
            rm = pat.search(text)
            if rm:
                title = rm.group("t").strip()
                break
        reqs = extract_requisition_ids(subject, body)
        job_related = bool(events) and events[0]["type"] != "job_alert"
        out = {
            "schema_version": "1.0",
            "is_job_related": job_related,
            "company": {"name": company, "confidence": 0.6 if company else 0.0},
            "role": {"title": title, "requisition_id": reqs[0] if reqs else None, "confidence": 0.6 if title else 0.0},
            "events": events,
            "overall_confidence": 0.6 if events else 0.3,
            "ambiguities": [],
            "needs_review": not company or not title,
        }
        return json.dumps(out)
