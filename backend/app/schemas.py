"""Typed request/response models for the REST API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Stage = Literal[
    "tracked", "ready", "submitted", "acknowledged", "assessment_invited", "assessment_completed",
    "screening", "interviewing", "offer", "accepted", "rejected", "withdrawn", "closed",
]
Category = Literal["PM", "TPM", "SE", "SA", "SWE", "Data", "Other"]
Channel = Literal["company_site", "ATS", "referral", "LinkedIn", "other"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MessageOut(BaseModel):
    message: str


class CompanyOut(ORM):
    id: str
    canonical_name: str
    website_domain: str | None = None


class AliasIn(BaseModel):
    alias: str = Field(min_length=1, max_length=200)
    alias_type: Literal["name", "domain", "ats_sender", "manual"] = "manual"
    verified: bool = True


class AliasOut(ORM):
    id: str
    company_id: str
    alias: str
    alias_type: str
    verified: bool


class ResumeIn(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    filename: str | None = None
    sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    local_path: str | None = None
    notes: str | None = None


class ResumeOut(ORM):
    id: str
    label: str
    filename: str | None
    sha256: str | None
    local_path: str | None
    notes: str | None
    created_at: datetime


class PostingIn(BaseModel):
    company: str
    title: str
    external_job_id: str | None = None
    posting_url: str | None = None
    location: str | None = None
    work_mode: str | None = None
    employment_type: str | None = None
    season: str | None = None
    description_excerpt: str | None = Field(None, max_length=2000)


class PostingOut(ORM):
    id: str
    company_id: str
    title: str
    external_job_id: str | None
    posting_url: str | None
    canonical_posting_url: str | None
    ats_platform: str | None
    location: str | None
    season: str | None
    source: str
    first_seen_at: datetime


class DuplicateCheckIn(BaseModel):
    company: str = Field(min_length=1)
    role_title: str | None = None
    external_job_id: str | None = None
    posting_url: str | None = None
    recruiting_cycle: str | None = None


class DuplicateCheckOut(BaseModel):
    decision: Literal["BLOCK", "WARN", "ALLOW"]
    reasons: list[str]
    matches: list[dict]
    company_id: str | None = None


class ApplicationCreate(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    role_title: str = Field(min_length=1, max_length=300)
    external_job_id: str | None = None
    posting_url: str | None = None
    recruiting_cycle: str | None = "Summer 2027"
    category: Category | None = None
    location: str | None = None
    application_channel: Channel | None = None
    resume_version_id: str | None = None
    stage: Stage = "tracked"
    # Only set when the user really submitted it; this makes the record "verified".
    submitted_at: datetime | None = None
    confirmed_submitted: bool = False
    notes: str | None = None
    override_duplicate: bool = False
    override_reason: str | None = None


class ApplicationUpdate(BaseModel):
    role_title: str | None = None
    external_job_id: str | None = None
    posting_url: str | None = None
    recruiting_cycle: str | None = None
    category: Category | None = None
    location: str | None = None
    application_channel: Channel | None = None
    resume_version_id: str | None = None
    notes: str | None = None
    submitted_at: datetime | None = None
    confirmed_submitted: bool | None = None


class StageUpdate(BaseModel):
    stage: Stage
    note: str | None = None
    lock: bool | None = None  # pin the stage against email-driven changes
    event_at: datetime | None = None


class ActionItemOut(ORM):
    id: str
    application_id: str
    assessment_id: str | None
    interview_id: str | None
    kind: str
    title: str
    instruction: str | None
    state: str
    due_at: datetime | None
    due_date: str | None
    due_timezone: str | None
    due_precision: str
    due_expression_raw: str | None
    due_anchor: str | None
    deadline_assumption: str | None
    deadline_confidence: float | None
    evidence_quote: str | None
    completed_at: datetime | None
    created_at: datetime
    urgency: str | None = None
    company: str | None = None
    role_title: str | None = None
    source_email_subject: str | None = None
    source_email_link: str | None = None


class ActionItemEdit(BaseModel):
    due_at: datetime | None = None
    due_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    note: str | None = None
    state: Literal["open", "completed", "cancelled", "expired", "needs_review"] | None = None


class EventOut(ORM):
    id: str
    application_id: str | None
    email_message_id: str | None
    event_type: str
    source: str
    event_at: datetime
    recorded_at: datetime
    from_stage: str | None
    to_stage: str | None
    description: str
    confidence: float | None
    evidence_quote: str | None
    evidence_locator: dict | None
    actor: str
    supersedes_event_id: str | None


class AssessmentOut(ORM):
    id: str
    provider: str | None
    assessment_type: str
    assessment_url: str | None
    invited_at: datetime
    completed_at: datetime | None
    state: str


class InterviewOut(ORM):
    id: str
    round_label: str | None
    starts_at: datetime | None
    original_timezone: str | None
    location_or_video_url: str | None
    state: str


class ApplicationOut(ORM):
    id: str
    company_id: str
    company: str
    role_title: str
    external_job_id: str | None
    posting_url: str | None
    ats_platform: str | None
    recruiting_cycle: str | None
    category: str | None
    location: str | None
    application_channel: str | None
    resume_version_id: str | None
    resume_label: str | None = None
    stage: str
    stage_updated_at: datetime
    submitted_at: datetime | None
    acknowledged_at: datetime | None
    submission_evidence_type: str | None
    verified: bool
    manual_stage_lock: bool
    notes: str | None
    source_reference: str | None
    created_at: datetime
    updated_at: datetime
    next_action: ActionItemOut | None = None
    pending_reviews: int = 0
    source: str | None = None


class EvidenceOut(BaseModel):
    email_message_id: str
    subject: str | None
    sender: str | None
    received_at: datetime | None
    excerpt: str | None
    web_link: str | None


class ApplicationDetailOut(ApplicationOut):
    events: list[EventOut]
    assessments: list[AssessmentOut]
    interviews: list[InterviewOut]
    action_items: list[ActionItemOut]
    evidence: list[EvidenceOut]
    proposals: list[dict]


class ReviewApproveIn(BaseModel):
    target_application_id: str | None = None
    create_new: bool = False
    notes: str | None = None
    edits: dict[str, Any] | None = Field(
        None,
        description="Optional overrides: company, role_title, external_job_id, posting_url, recruiting_cycle, "
        "category, stage, event_types {index: type}, conflict_resolution use_new|keep_existing, override_lock.",
    )


class ReviewRejectIn(BaseModel):
    notes: str | None = None


class SettingsUpdate(BaseModel):
    auto_apply_policy: Literal["review_first", "auto_harmless"] | None = None
    auto_apply_min_confidence: float | None = Field(None, ge=0.5, le=1.0)
    poll_interval_seconds: int | None = Field(None, ge=300, le=86400)
    auto_sync_enabled: bool | None = None
    initial_lookback_days: int | None = Field(None, ge=1, le=365)
    display_timezone: str | None = None
    desktop_notifications: bool | None = None
    ollama_model: str | None = None
    ollama_base_url: str | None = None
    missed_sync_warning_minutes: int | None = Field(None, ge=15, le=10080)


class NotificationRuleIn(BaseModel):
    lead_time_minutes: int = Field(ge=5, le=60 * 24 * 14)
    enabled: bool = True
    channel: Literal["dashboard", "desktop"] = "dashboard"


class NotificationRuleOut(ORM):
    id: str
    kind: str
    lead_time_minutes: int
    enabled: bool
    channel: str


class AccountOut(ORM):
    id: str
    provider: str
    address: str
    display_name: str | None
    auth_method: str
    enabled: bool
    sync_scope: dict
    connected: bool = False
    last_successful_sync_at: datetime | None
    last_attempt_at: datetime | None
    last_error_code: str | None


class AccountUpdate(BaseModel):
    enabled: bool | None = None
    display_name: str | None = None
    sync_scope: dict | None = None


class ImapAccountIn(BaseModel):
    address: str
    imap_host: str
    imap_port: int = 993
    use_ssl: bool = True
    password: str = Field(min_length=1)
    folders: list[str] = ["INBOX"]


class SubmissionReceiptIn(BaseModel):
    """Confirmed-submission receipt from an approved application tool (e.g. career-ops)."""

    source: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=200)
    confirmed: Literal[True] = Field(description="Must be true: only a confirmed submission is accepted.")
    receipt_id: str = Field(min_length=1, max_length=200, description="Confirmation number / receipt evidence.")
    company: str = Field(min_length=1, max_length=200)
    role_title: str = Field(min_length=1, max_length=300)
    external_job_id: str | None = None
    posting_url: str | None = None
    recruiting_cycle: str | None = "Summer 2027"
    submitted_at: datetime
    resume_version_id: str | None = None
    resume_label: str | None = None
    application_channel: Channel | None = "ATS"
    override_duplicate: bool = False
    override_reason: str | None = None


class SubmissionReceiptOut(BaseModel):
    application_id: str
    created: bool
    duplicate_check: DuplicateCheckOut
