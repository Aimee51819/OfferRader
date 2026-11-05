"""Parse -> validate -> evidence-check. Never writes to the database.

``extract(message_like, backend)`` returns an ``ExtractionOutcome``. Email text
is treated as inert data: it is delimited, length-limited, scrubbed of control
characters, and nothing from it is ever executed or used as an instruction.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import ValidationError

from ...config import settings
from ...domain.extraction_schema import SCHEMA_VERSION, Extraction, json_schema
from ...domain.normalize import (
    detect_assessment_provider,
    extract_requisition_ids,
    normalize_req_id,
)
from ..llm.ollama import LLMBackend, LLMError
from ..mail.prefilter import clean_body, is_blocked, is_otp_or_verification
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, USER_TEMPLATE

_RECRUITING_WORDS = re.compile(
    r"\b(applica\w*|applied|apply|candida\w*|interview\w*|assessment|recruit\w*|requisition|"
    r"intern(ship)?s?|offer|hiring|talent|position|role)\b",
    re.IGNORECASE,
)
_JOB_ALERT_SIGNALS = re.compile(
    r"(job alert|jobs? (?:you may|you might|for you|recommended)|recommended jobs|"
    r"new jobs? (?:matching|posted|that match)|top jobs|similar jobs|weekly digest|"
    r"based on your (?:profile|preferences|search)|apply now to|be an early applicant|"
    r"\d+\+? new jobs)",
    re.IGNORECASE,
)
_SUBMISSION_SIGNALS = re.compile(
    r"(thank you for (?:your )?appl|thanks for appl|we(?:'ve| have)? received your application|"
    r"your application (?:has been|was) (?:received|submitted)|application (?:received|submitted))",
    re.IGNORECASE,
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f​-‏ -‮⁠-⁤﻿]")


@dataclass
class MessageInput:
    sender: str
    subject: str
    body: str
    received_at: datetime | None
    sent_at: datetime | None = None


@dataclass
class ExtractionOutcome:
    extraction: Extraction | None
    model_name: str
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    parameters: dict = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)
    skipped_reason: str | None = None
    # Deterministic facts recorded alongside the model output.
    signals: dict = field(default_factory=dict)
    prepared_text: str = ""

    @property
    def ok(self) -> bool:
        return self.extraction is not None


def prepare_body(body: str, max_chars: int | None = None) -> str:
    """Deterministic preprocessing of an email body into model input text."""
    limit = max_chars or settings.max_model_input_chars
    text = html.unescape(body or "")
    text = _CONTROL.sub(" ", text)
    # Drop quoted reply history ("On Tue, ... wrote:" and "> " lines).
    text = re.split(r"\n\s*On .{5,120} wrote:\s*\n", text, maxsplit=1)[0]
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))
    text = clean_body(text, max_chars=limit * 2)
    # Neutralize anything that looks like our own delimiters.
    text = re.sub(r"</?\s*email\s*>", "[tag removed]", text, flags=re.IGNORECASE)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_in_source(quote: str, source: str) -> bool:
    q = _squash(quote).strip(" .\"'")
    return bool(q) and q in _squash(source)


def deterministic_signals(msg: MessageInput) -> dict:
    blob = f"{msg.subject}\n{msg.body}"
    return {
        "requisition_ids": extract_requisition_ids(msg.subject, msg.body),
        "assessment_provider": detect_assessment_provider(msg.sender, msg.body),
        "job_alert_signals": len(_JOB_ALERT_SIGNALS.findall(blob)),
        "submission_signals": len(_SUBMISSION_SIGNALS.findall(blob)),
        "has_unsubscribe": bool(re.search(r"unsubscribe", msg.body or "", re.IGNORECASE)),
    }


def prefilter(msg: MessageInput) -> str | None:
    """Return a skip reason, or None to continue to the model."""
    if is_otp_or_verification(msg.subject or "", msg.body or ""):
        return "otp_or_verification"
    blob = f"{msg.subject}\n{(msg.body or '')[:3000]}"
    if is_blocked(msg.sender or "") and not _RECRUITING_WORDS.search(blob):
        return "blocked_sender_no_recruiting_terms"
    return None


def _validate(raw: str) -> tuple[Extraction | None, list[str]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Tolerate a fenced block once; never "repair" beyond that.
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        if not m:
            return None, [f"invalid_json: {exc.msg}"]
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as exc2:
            return None, [f"invalid_json: {exc2.msg}"]
    if not isinstance(data, dict):
        return None, ["invalid_json: top-level value is not an object"]
    try:
        return Extraction.model_validate(data), []
    except ValidationError as exc:
        return None, [f"schema: {'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()][:20]


def _post_validate(ext: Extraction, msg: MessageInput, prepared: str, signals: dict) -> list[str]:
    """Evidence checks + deterministic cross-checks. Mutates ext; returns notes."""
    notes: list[str] = []
    source = f"{msg.subject}\n{msg.body}"
    for ev in ext.events:
        kept = [q for q in ev.evidence_quotes if quote_in_source(q, source)]
        if len(kept) != len(ev.evidence_quotes):
            notes.append(f"{ev.type}: dropped {len(ev.evidence_quotes) - len(kept)} unsupported quote(s)")
        ev.evidence_quotes = kept
        if not kept and ev.type not in {"job_alert", "other"}:
            ev.confidence = min(ev.confidence, 0.5)
            ext.needs_review = True
            ext.ambiguities.append(f"No verbatim evidence for '{ev.type}'.")
        if ev.deadline and ev.deadline.raw_expression:
            if not quote_in_source(ev.deadline.raw_expression, source):
                notes.append(f"{ev.type}: deadline wording not found verbatim")
                ext.needs_review = True
                ext.ambiguities.append(
                    f"Deadline wording '{ev.deadline.raw_expression}' is not in the email."
                )
                ev.deadline.confidence = min(ev.deadline.confidence, 0.3)
        if ev.type in {"assessment_invitation", "assessment_reminder"} and not ev.assessment_provider:
            ev.assessment_provider = signals.get("assessment_provider")

    # Requisition id: must appear in the email; fill from regex when the model omitted it.
    rid = ext.role.requisition_id
    if rid and not quote_in_source(rid, source):
        notes.append("requisition_id not in email; cleared")
        ext.role.requisition_id = None
        rid = None
    if not rid and len(signals["requisition_ids"]) == 1:
        ext.role.requisition_id = signals["requisition_ids"][0]
        notes.append("requisition_id filled from deterministic regex")
    elif not rid and len(signals["requisition_ids"]) > 1:
        ext.ambiguities.append(
            "Several requisition ids in email: " + ", ".join(signals["requisition_ids"])
        )
        ext.needs_review = True
    if ext.role.requisition_id:
        ext.role.requisition_id = normalize_req_id(ext.role.requisition_id)

    # Company / role names must be supported by the email text or sender.
    sender_blob = f"{msg.sender}\n{source}"
    if ext.company.name and not quote_in_source(ext.company.name, sender_blob):
        ext.company.confidence = min(ext.company.confidence, 0.4)
        ext.needs_review = True
        ext.ambiguities.append(f"Company '{ext.company.name}' is not named in the email.")
    if ext.role.title and not quote_in_source(ext.role.title, source):
        ext.role.confidence = min(ext.role.confidence, 0.5)
        ext.ambiguities.append(f"Role '{ext.role.title}' is not written verbatim in the email.")

    # Newsletter / alert guard: alert-like mail claiming a submission needs a human.
    claims_submission = any(
        e.type in {"application_confirmation", "application_received"} for e in ext.events
    )
    if claims_submission and signals["job_alert_signals"] and not signals["submission_signals"]:
        ext.needs_review = True
        ext.ambiguities.append("Looks like a job alert/newsletter but was classified as a submission.")
    return notes


def extract(msg: MessageInput, backend: LLMBackend) -> ExtractionOutcome:
    signals = deterministic_signals(msg)
    reason = prefilter(msg)
    if reason:
        return ExtractionOutcome(
            extraction=Extraction(is_job_related=False, overall_confidence=1.0),
            model_name="prefilter",
            skipped_reason=reason,
            signals=signals,
        )
    prepared = prepare_body(msg.body)
    user = USER_TEMPLATE.format(
        sender=(msg.sender or "")[:300].replace("\n", " "),
        subject=(msg.subject or "")[:300].replace("\n", " "),
        received_at=msg.received_at.isoformat() if msg.received_at else "unknown",
        body=prepared,
    )
    schema = json_schema()
    errors: list[str] = []
    ext: Extraction | None = None
    attempts = 2
    for attempt in range(attempts):
        prompt_user = user
        if errors:
            prompt_user += (
                "\n\nYour previous output was rejected: "
                + "; ".join(errors[:5])
                + ". Return only a valid JSON object for the schema."
            )
        raw = backend.chat_json(SYSTEM_PROMPT, prompt_user, schema)  # may raise LLMError
        ext, errs = _validate(raw)
        if ext is not None:
            break
        errors = errs
    outcome = ExtractionOutcome(
        extraction=ext,
        model_name=backend.name,
        parameters=dict(getattr(backend, "parameters", {}) or {}),
        validation_errors=errors if ext is None else [],
        signals=signals,
        prepared_text=prepared,
    )
    if ext is not None:
        outcome.validation_errors = _post_validate(ext, msg, prepared, signals)
    return outcome


__all__ = ["ExtractionOutcome", "LLMError", "MessageInput", "extract", "prepare_body"]
