"""Typed ORM schema for the Job Application Feedback Agent.

Conventions
-----------
* IDs are UUID4 strings.
* Every instant is stored as a UTC ISO-8601 string (``UTCDateTime``) so that
  SQLite keeps the offset and lexical order equals chronological order.
* ``application_events`` is append-only history; ``applications.stage`` is only
  a summary of that history.
* Enum-like columns are plain TEXT validated in Python (see ``domain/enums.py``)
  so new values never need a table rebuild in SQLite.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Aware datetime <-> 'YYYY-MM-DDTHH:MM:SS.ffffffZ' text (always UTC)."""

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> str | None:  # noqa: ANN001
        if value is None:
            return None
        if isinstance(value, str):
            value = parse_utc(value)
        if not isinstance(value, datetime):
            raise TypeError(f"UTCDateTime expects datetime, got {type(value)!r}")
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not allowed; attach a timezone first.")
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def process_result_value(self, value: Any, dialect) -> datetime | None:  # noqa: ANN001
        if value is None:
            return None
        return parse_utc(value)


def parse_utc(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _pk() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=new_id)


# ---------------------------------------------------------------------------
# Companies / postings / resumes
# ---------------------------------------------------------------------------


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = _pk()
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    website_domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    aliases: Mapped[list[CompanyAlias]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class CompanyAlias(Base):
    __tablename__ = "company_aliases"
    __table_args__ = (
        UniqueConstraint("company_id", "normalized_alias", "alias_type", name="uq_alias"),
        Index("ix_alias_lookup", "normalized_alias", "alias_type"),
    )

    id: Mapped[str] = _pk()
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(Text)
    normalized_alias: Mapped[str] = mapped_column(Text)
    alias_type: Mapped[str] = mapped_column(String(16))  # name|domain|ats_sender|manual
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    company: Mapped[Company] = relationship(back_populates="aliases")


class JobPosting(Base):
    __tablename__ = "job_postings"
    __table_args__ = (
        Index("ix_posting_company_title", "company_id", "normalized_title"),
        Index("ix_posting_external_id", "external_job_id"),
        Index("ix_posting_canonical_url", "canonical_posting_url"),
    )

    id: Mapped[str] = _pk()
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    normalized_title: Mapped[str] = mapped_column(Text)
    external_job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    posting_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_posting_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ats_platform: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    employment_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    season: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, default="manual")
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    description_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)

    company: Mapped[Company] = relationship()


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id: Mapped[str] = _pk()
    label: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        Index("ix_app_stage_updated", "stage", "updated_at"),
        Index("ix_app_company_role", "company_id", "normalized_role_title"),
        Index("ix_app_external_job_id", "external_job_id"),
    )

    id: Mapped[str] = _pk()
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), index=True
    )
    job_posting_id: Mapped[str | None] = mapped_column(
        ForeignKey("job_postings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role_title: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_role_title: Mapped[str] = mapped_column(Text, default="")
    external_job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    posting_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_posting_url: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    ats_platform: Mapped[str | None] = mapped_column(Text, nullable=True)
    recruiting_cycle: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(8), nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resume_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("resume_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="tracked")
    stage_updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # Time of the newest evidence that moved/confirmed the summary stage. Used to
    # stop late-arriving older mail from rolling the stage back.
    stage_evidence_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    submission_evidence_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    submission_receipt_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    submission_idempotency_key: Mapped[str | None] = mapped_column(
        Text, nullable=True, unique=True
    )
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    manual_stage_lock: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    company: Mapped[Company] = relationship()
    job_posting: Mapped[JobPosting | None] = relationship()
    resume_version: Mapped[ResumeVersion | None] = relationship()

    @property
    def is_verified_submission(self) -> bool:
        return self.submission_evidence_type is not None and self.submitted_at is not None


# ---------------------------------------------------------------------------
# Mail
# ---------------------------------------------------------------------------


class EmailAccount(Base):
    __tablename__ = "email_accounts"
    __table_args__ = (UniqueConstraint("provider", "address", name="uq_account_address"),)

    id: Mapped[str] = _pk()
    provider: Mapped[str] = mapped_column(String(16))  # gmail|outlook|imap|fake
    address: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_method: Mapped[str] = mapped_column(String(32))  # oauth_pkce|imap_password|none
    credential_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_scope: Mapped[dict] = mapped_column(JSON, default=dict)
    sync_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class EmailMessage(Base):
    __tablename__ = "email_messages"
    __table_args__ = (
        UniqueConstraint("email_account_id", "provider_message_id", name="uq_account_message"),
        Index("ix_msg_account_received", "email_account_id", "received_at"),
        Index("ix_msg_state", "processing_state"),
        Index("ix_msg_internet_id", "internet_message_id"),
    )

    id: Mapped[str] = _pk()
    email_account_id: Mapped[str] = mapped_column(
        ForeignKey("email_accounts.id", ondelete="CASCADE"), index=True
    )
    provider_message_id: Mapped[str] = mapped_column(Text)
    provider_thread_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    internet_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipient_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    body_hash: Mapped[str] = mapped_column(String(64))
    evidence_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_cache_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    web_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # pending -> parsed|skipped|failed|ignored ; failed retries are bounded.
    processing_state: Mapped[str] = mapped_column(String(16), default="pending")
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0)

    account: Mapped[EmailAccount] = relationship()


class ParsedMessage(Base):
    __tablename__ = "parsed_messages"

    id: Mapped[str] = _pk()
    email_message_id: Mapped[str] = mapped_column(
        ForeignKey("email_messages.id", ondelete="CASCADE"), index=True
    )
    schema_version: Mapped[str] = mapped_column(String(16))
    prompt_version: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(Text)
    model_parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    is_job_related: Mapped[bool] = mapped_column(Boolean, default=False)
    extracted_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_errors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    parsed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------------------
# History, assessments, interviews, action items
# ---------------------------------------------------------------------------


class ApplicationEvent(Base):
    __tablename__ = "application_events"
    __table_args__ = (Index("ix_event_app_time", "application_id", "event_at"),)

    id: Mapped[str] = _pk()
    application_id: Mapped[str | None] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=True, index=True
    )
    email_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("email_messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(16))  # email|manual|agent|import
    event_at: Mapped[datetime] = mapped_column(UTCDateTime)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    from_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_locator: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=True)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    # Set on a corrective event; the original row is never edited or deleted.
    supersedes_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("application_events.id", ondelete="SET NULL"), nullable=True, index=True
    )


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[str] = _pk()
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    invitation_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("application_events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessment_type: Mapped[str] = mapped_column(String(16), default="OA")
    assessment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    invited_at: Mapped[datetime] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="invited")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[str] = _pk()
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    source_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("application_events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    round_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    original_timezone: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_or_video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="proposed")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ActionItem(Base):
    __tablename__ = "action_items"
    __table_args__ = (
        Index("ix_action_state_due", "state", "due_at"),
        CheckConstraint(
            "due_precision IN ('instant','day','relative','unknown')", name="ck_due_precision"
        ),
    )

    id: Mapped[str] = _pk()
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    assessment_id: Mapped[str | None] = mapped_column(
        ForeignKey("assessments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    interview_id: Mapped[str | None] = mapped_column(
        ForeignKey("interviews.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("application_events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(Text)
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="open")
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # For day-precision deadlines: the calendar date as written (no invented time).
    due_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    due_timezone: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_precision: Mapped[str] = mapped_column(String(16), default="unknown")
    due_expression_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_anchor: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline_assumption: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    application: Mapped[Application] = relationship()


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


class ReviewProposal(Base):
    __tablename__ = "review_proposals"
    __table_args__ = (Index("ix_review_state_created", "state", "created_at"),)

    id: Mapped[str] = _pk()
    email_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("email_messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parsed_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("parsed_messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    target_application_id: Mapped[str | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"), nullable=True, index=True
    )
    proposal_type: Mapped[str] = mapped_column(String(24))
    proposed_changes: Mapped[dict] = mapped_column(JSON, default=dict)
    alternatives: Mapped[list | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stable identity so re-processing the same message never duplicates a proposal.
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    # Events created when this proposal was applied (for undo).
    applied_event_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Notifications / sync / settings
# ---------------------------------------------------------------------------


class NotificationRule(Base):
    __tablename__ = "notification_rules"

    id: Mapped[str] = _pk()
    kind: Mapped[str] = mapped_column(String(24), default="deadline")
    lead_time_minutes: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    channel: Mapped[str] = mapped_column(String(16), default="dashboard")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class NotificationLog(Base):
    __tablename__ = "notification_log"
    __table_args__ = (
        UniqueConstraint(
            "action_item_id", "rule_id", "scheduled_for_at", name="uq_notification_once"
        ),
    )

    id: Mapped[str] = _pk()
    action_item_id: Mapped[str] = mapped_column(
        ForeignKey("action_items.id", ondelete="CASCADE"), index=True
    )
    rule_id: Mapped[str] = mapped_column(
        ForeignKey("notification_rules.id", ondelete="CASCADE"), index=True
    )
    scheduled_for_at: Mapped[datetime] = mapped_column(UTCDateTime)
    triggered_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    delivery_state: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[str] = _pk()
    email_account_id: Mapped[str] = mapped_column(
        ForeignKey("email_accounts.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    messages_seen: Mapped[int] = mapped_column(Integer, default=0)
    messages_new: Mapped[int] = mapped_column(Integer, default=0)
    messages_parsed: Mapped[int] = mapped_column(Integer, default=0)
    proposals_created: Mapped[int] = mapped_column(Integer, default=0)
    events_applied: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    safe_error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class Credential(Base):
    """Encrypted-at-rest credential blob. ``email_accounts.credential_ref`` points here.

    The ciphertext is Fernet (AES-128-CBC + HMAC) with a key kept in the OS
    keychain when available, else DATA_DIR/secret.key (0600). Never logged or
    returned by the API.
    """

    __tablename__ = "credentials"

    id: Mapped[str] = _pk()
    kind: Mapped[str] = mapped_column(String(32))  # oauth_token|imap_password|integration
    ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
