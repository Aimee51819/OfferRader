"""Versioned JSON contract between the local Qwen model and the pipeline.

The model must return exactly this shape. Anything that fails validation is
recorded in ``parsed_messages.validation_errors`` and never touches an
application. Unknown values are ``null`` — never guessed.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"

EventType = Literal[
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
]
DeadlineKind = Literal["absolute", "date_only", "relative", "business_days", "none", "unknown"]
Anchor = Literal["email_received_at", "email_sent_at", "invitation_opened", "other"]
Precision = Literal["instant", "day", "relative", "unknown"]
AssessmentType = Literal["OA", "coding", "case", "video", "personality", "other"]

_MAX_QUOTE = 300


def _clip(v: str | None, n: int) -> str | None:
    if v is None:
        return None
    v = str(v).strip()
    return v[:n] if v else None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class CompanyField(_Strict):
    name: str | None = None
    domain: str | None = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)

    @field_validator("name", "domain")
    @classmethod
    def _short(cls, v: str | None) -> str | None:
        return _clip(v, 200)


class RoleField(_Strict):
    title: str | None = None
    requisition_id: str | None = None
    job_url: str | None = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)

    @field_validator("title", "requisition_id")
    @classmethod
    def _short(cls, v: str | None) -> str | None:
        return _clip(v, 200)

    @field_validator("job_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        from .normalize import safe_http_url

        return safe_http_url(_clip(v, 2048))


class Deadline(_Strict):
    raw_expression: str | None = None
    kind: DeadlineKind = "unknown"
    anchor: Anchor | None = None
    timezone_text: str | None = None
    date_only: str | None = None  # YYYY-MM-DD as written, no invented time
    normalized_at_utc: str | None = None  # ignored by the pipeline; recomputed deterministically
    precision: Precision = "unknown"
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0)

    @field_validator("raw_expression", "timezone_text")
    @classmethod
    def _short(cls, v: str | None) -> str | None:
        return _clip(v, _MAX_QUOTE)


class Event(_Strict):
    type: EventType
    event_time: str | None = None  # e.g. interview start as written / ISO if explicit
    event_time_raw: str | None = None
    action_required: str | None = None
    assessment_type: AssessmentType | None = None
    assessment_provider: str | None = None
    action_url: str | None = None
    deadline: Deadline | None = None
    evidence_quotes: list[str] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0)

    @field_validator("action_required", "assessment_provider", "event_time", "event_time_raw")
    @classmethod
    def _short(cls, v: str | None) -> str | None:
        return _clip(v, _MAX_QUOTE)

    @field_validator("action_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        from .normalize import safe_http_url

        return safe_http_url(_clip(v, 2048))

    @field_validator("evidence_quotes")
    @classmethod
    def _quotes(cls, v: list[str]) -> list[str]:
        return [q.strip()[:_MAX_QUOTE] for q in v if isinstance(q, str) and q.strip()][:5]


class Extraction(_Strict):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    is_job_related: bool
    language: str | None = "en"
    company: CompanyField = Field(default_factory=CompanyField)
    role: RoleField = Field(default_factory=RoleField)
    events: list[Event] = Field(default_factory=list, max_length=8)
    overall_confidence: float = Field(0.0, ge=0.0, le=1.0)
    ambiguities: list[str] = Field(default_factory=list)
    needs_review: bool = False

    @model_validator(mode="after")
    def _consistency(self) -> Extraction:
        if not self.is_job_related and any(
            e.type not in {"job_alert", "other"} for e in self.events
        ):
            # Contradiction: not job-related yet has recruiting events -> force review.
            self.needs_review = True
            self.ambiguities.append("is_job_related=false but recruiting events present")
        self.ambiguities = [a[:_MAX_QUOTE] for a in self.ambiguities][:10]
        return self


def json_schema() -> dict:
    """Schema passed to Ollama's structured-output ``format`` parameter."""
    schema = Extraction.model_json_schema()
    # Pydantic only lists fields without defaults as required. Under constrained
    # decoding the model may then close an object early and omit company/role/events
    # or an event's deadline, so require every property of every object (nullable
    # fields can still be null).
    def _require_all(node):
        if isinstance(node, dict):
            if "properties" in node:
                node["required"] = list(node["properties"])
            for v in node.values():
                _require_all(v)
        elif isinstance(node, list):
            for v in node:
                _require_all(v)

    _require_all(schema)
    return schema


EXAMPLE = {
    "schema_version": "1.0",
    "is_job_related": True,
    "language": "en",
    "company": {"name": "Example Corp", "domain": None, "confidence": 0.98},
    "role": {
        "title": "Product Manager Intern",
        "requisition_id": "JR-123",
        "job_url": None,
        "confidence": 0.91,
    },
    "events": [
        {
            "type": "assessment_invitation",
            "event_time": None,
            "action_required": "Complete the online assessment",
            "assessment_type": "OA",
            "assessment_provider": None,
            "action_url": None,
            "deadline": {
                "raw_expression": "within 72 hours of receiving this email",
                "kind": "relative",
                "anchor": "email_received_at",
                "timezone_text": None,
                "date_only": None,
                "normalized_at_utc": None,
                "precision": "relative",
                "assumptions": [],
                "confidence": 0.95,
            },
            "evidence_quotes": ["within 72 hours of receiving this email"],
            "confidence": 0.95,
        }
    ],
    "overall_confidence": 0.94,
    "ambiguities": [],
    "needs_review": False,
}
