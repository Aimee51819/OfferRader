"""Closed vocabularies used across the schema, API, and pipeline."""
from __future__ import annotations

STAGES = (
    "tracked",
    "ready",
    "submitted",
    "acknowledged",
    "assessment_invited",
    "assessment_completed",
    "screening",
    "interviewing",
    "offer",
    "accepted",
    "rejected",
    "withdrawn",
    "closed",
)
TERMINAL_STAGES = frozenset({"offer", "accepted", "rejected", "withdrawn", "closed"})
# Ordering for the "progress" summary. Terminal stages are handled separately.
STAGE_RANK = {
    "tracked": 0,
    "ready": 1,
    "submitted": 2,
    "acknowledged": 3,
    "assessment_invited": 4,
    "assessment_completed": 5,
    "screening": 6,
    "interviewing": 7,
    "offer": 8,
    "accepted": 9,
}
# Stages that mean "an application really exists".
SUBMITTED_OR_LATER = frozenset(STAGES) - {"tracked", "ready"}

EVENT_TYPES = (
    "application_confirmation",
    "application_received",
    "assessment_invitation",
    "assessment_reminder",
    "assessment_completed",
    "screening",
    "interview_invitation",
    "interview_rescheduled",
    "interview_cancelled",
    "rejection",
    "offer",
    "withdrawal",
    "portal_update",
    "job_alert",
    "other",
)
# Events that never touch an application on their own.
NOISE_EVENT_TYPES = frozenset({"job_alert", "other"})
# Events that need human review by default before they change a stage.
SENSITIVE_EVENT_TYPES = frozenset({"rejection", "offer", "withdrawal"})

# Which summary stage an email event implies (None = no stage change).
EVENT_TO_STAGE: dict[str, str | None] = {
    "application_confirmation": "acknowledged",
    "application_received": "acknowledged",
    "assessment_invitation": "assessment_invited",
    "assessment_reminder": None,
    "assessment_completed": "assessment_completed",
    "screening": "screening",
    "interview_invitation": "interviewing",
    "interview_rescheduled": "interviewing",
    "interview_cancelled": None,
    "rejection": "rejected",
    "offer": "offer",
    "withdrawal": "withdrawn",
    "portal_update": None,
    "job_alert": None,
    "other": None,
}

CATEGORIES = ("PM", "SE", "SDE/SWE", "Data", "Other")
CHANNELS = ("company_site", "ATS", "referral", "LinkedIn", "other")
SUBMISSION_EVIDENCE = ("manual_confirmed", "agent_receipt", "acknowledgment_email")
EVENT_SOURCES = ("email", "manual", "agent", "import")
ASSESSMENT_TYPES = ("OA", "coding", "case", "video", "personality", "other")
ASSESSMENT_STATES = ("invited", "started", "completed", "expired", "cancelled", "unknown")
INTERVIEW_STATES = ("proposed", "scheduled", "rescheduled", "completed", "cancelled")
ACTION_KINDS = (
    "oa_deadline",
    "interview",
    "document_request",
    "schedule_interview",
    "follow_up",
    "other",
)
ACTION_STATES = ("open", "completed", "cancelled", "expired", "needs_review")
DUE_PRECISIONS = ("instant", "day", "relative", "unknown")
PROPOSAL_TYPES = ("match", "status", "deadline", "create_application", "merge", "other")
PROPOSAL_STATES = ("pending", "approved", "edited", "rejected")
PROVIDERS = ("gmail", "outlook", "imap", "fake")
