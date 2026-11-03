from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ApplicationStatus(str, enum.Enum):
    saved = "saved"
    applied = "applied"
    online_assessment = "online_assessment"
    phone_screen = "phone_screen"
    interview = "interview"
    offer = "offer"
    rejected = "rejected"
    ghosted = "ghosted"
    accepted = "accepted"


class EventSource(str, enum.Enum):
    manual = "manual"
    email = "email"
    system = "system"


class SuggestionStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class SuggestionKind(str, enum.Enum):
    status_change = "status_change"
    new_application = "new_application"
    note = "note"


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    salary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, native_enum=False, length=32),
        default=ApplicationStatus.saved,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    skills: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Employer req / posting id (e.g. IBM "128506") — distinguishes multiple roles
    # at the same company.
    job_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    date_applied: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    events: Mapped[list["StatusEvent"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="StatusEvent.created_at.desc(), StatusEvent.id.desc()",
    )
    suggestions: Mapped[list["Suggestion"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class StatusEvent(Base):
    __tablename__ = "status_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[ApplicationStatus | None] = mapped_column(
        Enum(ApplicationStatus, native_enum=False, length=32), nullable=True
    )
    to_status: Mapped[ApplicationStatus | None] = mapped_column(
        Enum(ApplicationStatus, native_enum=False, length=32), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[EventSource] = mapped_column(Enum(EventSource), default=EventSource.manual)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    application: Mapped["Application"] = relationship(back_populates="events")


class DismissedDuplicate(Base):
    """A pair the user said is not a duplicate, so it stops being suggested.

    Stored order-independently: low_id is always the smaller application id.
    SQLite doesn't enforce FKs here, so deleting/merging an application also
    removes its rows explicitly (see api/applications.py).
    """

    __tablename__ = "dismissed_duplicates"
    __table_args__ = (UniqueConstraint("low_id", "high_id", name="uq_dismissed_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    low_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    high_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    imap_host: Mapped[str] = mapped_column(String(255))
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    username: Mapped[str] = mapped_column(String(255))
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    folder: Mapped[str] = mapped_column(String(255), default="INBOX")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ProcessedEmail(Base):
    __tablename__ = "processed_emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("email_accounts.id", ondelete="CASCADE"), index=True
    )
    message_uid: Mapped[str] = mapped_column(String(255), index=True)
    message_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender: Mapped[str | None] = mapped_column(String(512), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_job_related: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Suggestion(Base):
    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[SuggestionKind] = mapped_column(Enum(SuggestionKind))
    status: Mapped[SuggestionStatus] = mapped_column(
        Enum(SuggestionStatus), default=SuggestionStatus.pending, index=True
    )
    suggested_status: Mapped[ApplicationStatus | None] = mapped_column(
        Enum(ApplicationStatus, native_enum=False, length=32), nullable=True
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-100
    email_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_sender: Mapped[str | None] = mapped_column(String(512), nullable=True)
    email_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When the email was actually sent. Timelines and ordering use this rather
    # than created_at, which is only when the sync happened to run.
    email_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    application: Mapped["Application | None"] = relationship(back_populates="suggestions")


class LLMProvider(Base):
    __tablename__ = "llm_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))  # user-friendly label
    provider: Mapped[str] = mapped_column(String(64))  # openai|anthropic|gemini|ollama|...
    model: Mapped[str] = mapped_column(String(255))
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_base: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
