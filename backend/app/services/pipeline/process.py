"""Process stored messages: extract -> record provenance -> plan -> route.

Each message is handled in its own transaction. A model/validation failure
records the error on the message and leaves every application untouched.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Application, EmailMessage, ParsedMessage, ReviewProposal
from .. import settings_service
from ..extraction.extractor import ExtractionOutcome, MessageInput, extract
from ..llm.ollama import LLMBackend, LLMError
from .apply import apply_plan, build_plan, create_application_from_plan, message_identity, route_plan
from .ingest import drop_cached_body, load_cached_body

logger = logging.getLogger("tracker.process")

MAX_ATTEMPTS = 5


@dataclass
class ProcessStats:
    parsed: int = 0
    ignored: int = 0
    proposals: int = 0
    events_applied: int = 0
    failed: int = 0
    duplicates: int = 0
    aborted_reason: str | None = None
    errors: list[str] = field(default_factory=list)


def _duplicate_of(db: Session, msg: EmailMessage) -> EmailMessage | None:
    if not msg.internet_message_id:
        return None
    return db.execute(
        select(EmailMessage).where(
            EmailMessage.internet_message_id == msg.internet_message_id,
            EmailMessage.id != msg.id,
            EmailMessage.processing_state.in_(("parsed", "ignored", "skipped")),
        )
    ).scalars().first()


def record_parse(db: Session, msg: EmailMessage, outcome: ExtractionOutcome) -> ParsedMessage:
    ext = outcome.extraction
    pm = ParsedMessage(
        email_message_id=msg.id,
        schema_version=outcome.schema_version,
        prompt_version=outcome.prompt_version,
        model_name=outcome.model_name,
        model_parameters={**outcome.parameters, "signals": outcome.signals, "skipped": outcome.skipped_reason},
        is_job_related=bool(ext and ext.is_job_related),
        extracted_payload=ext.model_dump(mode="json") if ext else None,
        overall_confidence=ext.overall_confidence if ext else None,
        validation_errors=outcome.validation_errors or None,
    )
    db.add(pm)
    db.flush()
    return pm


def _create_app(db: Session, plan: dict, msg: EmailMessage) -> Application | None:
    """Create the application for a plan with no match. Missing company/role fall back
    to the sender name / a placeholder rather than dropping the email."""
    edits: dict = {"actor": "agent:auto_all"}
    company_id = plan["company"].get("company_id")
    if not plan["role"].get("title") and company_id:
        # No role in the email: if this company has exactly one application, it is that one.
        existing = db.execute(select(Application).where(Application.company_id == company_id)).scalars().all()
        if len(existing) == 1:
            return existing[0]
    if not (plan["company"].get("resolved_name") or plan["company"].get("name")):
        if not msg.sender_name:
            return None
        edits["company"] = msg.sender_name
    if not plan["role"].get("title"):
        edits["role_title"] = "Role not stated"
    return create_application_from_plan(db, plan, edits)


def message_input(msg: EmailMessage) -> MessageInput:
    body = load_cached_body(msg.body_cache_ref)
    if body is None:
        body = msg.evidence_excerpt or ""
    return MessageInput(
        sender=f"{msg.sender_name or ''} <{msg.sender_address or ''}>".strip(),
        subject=msg.subject or "",
        body=body,
        received_at=msg.received_at,
        sent_at=msg.sent_at,
    )


def process_message(
    db: Session,
    msg: EmailMessage,
    backend: LLMBackend,
    *,
    policy: str | None = None,
    min_confidence: float | None = None,
    dry_run: bool = False,
    outcome: ExtractionOutcome | None = None,
) -> dict:
    """Process one stored message. Raises LLMError (caller decides to stop).

    Pass ``outcome`` when the extraction was already run outside the transaction."""
    policy = policy or settings_service.get(db, "auto_apply_policy")
    min_confidence = (
        min_confidence if min_confidence is not None else float(settings_service.get(db, "auto_apply_min_confidence"))
    )
    dup = _duplicate_of(db, msg)
    if dup is not None:
        msg.processing_state = "skipped"
        msg.processing_error = f"duplicate of message {dup.id} (same Message-ID in another inbox)"
        drop_cached_body(msg.body_cache_ref)
        msg.body_cache_ref = None
        return {"state": "duplicate"}

    if outcome is None:
        outcome = extract(message_input(msg), backend)  # LLMError propagates; nothing written yet
    msg.processing_attempts = (msg.processing_attempts or 0) + 1
    parsed = record_parse(db, msg, outcome)
    if outcome.extraction is None:
        msg.processing_state = "failed"
        msg.processing_error = "; ".join(outcome.validation_errors)[:500] or "invalid model output"
        return {"state": "invalid", "errors": outcome.validation_errors}

    ext = outcome.extraction
    actionable = [e for e in ext.events if e.type not in {"job_alert", "other"}]
    if not ext.is_job_related or not actionable:
        if ext.needs_review and actionable and policy != "auto_all":
            pass  # contradictory output: fall through to review (auto_all: never auto-create)
        else:
            msg.processing_state = "ignored"
            msg.processing_error = None
            drop_cached_body(msg.body_cache_ref)
            msg.body_cache_ref = None
            return {"state": "ignored", "reason": outcome.skipped_reason or "not an application event"}

    plan = build_plan(db, msg, ext)
    target = db.get(Application, plan["target_application_id"]) if plan["target_application_id"] else None
    auto, ptype, reason = route_plan(plan, policy=policy, min_confidence=min_confidence, app=target)
    if dry_run:
        db.rollback()
        return {"state": "dry_run", "auto_apply": auto, "proposal_type": ptype, "reason": reason, "plan": plan}

    msg.processing_state = "parsed"
    msg.processing_error = None
    result: dict = {"state": "parsed", "auto_apply": auto, "proposal_type": ptype, "reason": reason}
    if policy == "auto_all":
        app = target or _create_app(db, plan, msg)
        if app is not None:
            result["auto_apply"] = True
            result["events"] = apply_plan(db, plan, app, actor="agent:auto_all", human_approved=True)
            result["application_id"] = app.id
    elif auto and target is not None:
        ids = apply_plan(db, plan, target, actor="agent:auto_policy", human_approved=False)
        result["events"] = ids
    else:
        key = f"proposal:{message_identity(msg)}"
        existing = db.execute(select(ReviewProposal).where(ReviewProposal.idempotency_key == key)).scalar_one_or_none()
        if existing is None:
            db.add(
                ReviewProposal(
                    email_message_id=msg.id,
                    parsed_message_id=parsed.id,
                    target_application_id=plan["target_application_id"],
                    proposal_type=ptype,
                    proposed_changes=plan,
                    alternatives=plan["alternatives"],
                    reason=reason,
                    confidence=float(plan["overall_confidence"] or 0.0),
                    state="pending",
                    idempotency_key=key,
                )
            )
            result["proposal_created"] = True
        else:
            result["proposal_created"] = False
    # Body is no longer needed once a validated parse exists (evidence excerpt is kept).
    drop_cached_body(msg.body_cache_ref)
    msg.body_cache_ref = None
    return result


def process_pending(db: Session, backend: LLMBackend, *, limit: int = 200) -> ProcessStats:
    stats = ProcessStats()
    rows = db.execute(
        select(EmailMessage)
        .where(
            EmailMessage.processing_state.in_(("pending", "failed")),
            EmailMessage.processing_attempts < MAX_ATTEMPTS,
        )
        .order_by(EmailMessage.received_at.asc())
        .limit(limit)
    ).scalars().all()
    for msg in rows:
        try:
            # Run the slow local-model call with no open transaction, so the sync never
            # holds the SQLite write lock while waiting on the model (UI writes and the
            # scheduler heartbeat would otherwise time out).
            inp = message_input(msg)
            db.commit()
            outcome = extract(inp, backend)
            with db.begin_nested():
                out = process_message(db, msg, backend, outcome=outcome)
            db.commit()
        except LLMError as exc:
            db.rollback()
            # Model is down/misconfigured: stop; everything stays pending for the next run.
            msg = db.get(EmailMessage, msg.id)
            if msg is not None:
                msg.processing_error = f"{exc.code}: {str(exc)[:200]}"
                db.commit()
            stats.aborted_reason = exc.code
            stats.errors.append(str(exc)[:300])
            break
        except Exception as exc:  # noqa: BLE001 - one bad message must not stop the run
            db.rollback()
            logger.exception("Processing failed for message %s", msg.id)
            msg = db.get(EmailMessage, msg.id)
            if msg is not None:
                msg.processing_state = "failed"
                msg.processing_attempts = (msg.processing_attempts or 0) + 1
                msg.processing_error = f"internal_error: {type(exc).__name__}"
                db.commit()
            stats.failed += 1
            continue
        state = out.get("state")
        if state == "parsed":
            stats.parsed += 1
            if out.get("proposal_created"):
                stats.proposals += 1
            stats.events_applied += len(out.get("events", []))
        elif state == "ignored":
            stats.ignored += 1
        elif state == "duplicate":
            stats.duplicates += 1
        elif state == "invalid":
            stats.failed += 1
    return stats
