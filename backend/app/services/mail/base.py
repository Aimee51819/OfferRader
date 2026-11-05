"""Provider-neutral mail types. Every adapter is strictly read-only."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from collections.abc import Iterator
from typing import Protocol


@dataclass
class ProviderMessage:
    provider_message_id: str
    thread_id: str | None
    internet_message_id: str | None
    sender_address: str | None
    sender_name: str | None
    recipient_address: str | None
    subject: str
    sent_at: datetime | None
    received_at: datetime | None
    body_text: str
    web_link: str | None = None
    folder: str | None = None


@dataclass
class FetchPage:
    messages: list[ProviderMessage]
    # Cursor to persist only after every message in this page is stored.
    next_cursor: str | None
    has_more: bool = False
    removed_ids: list[str] = field(default_factory=list)


class MailError(Exception):
    code = "mail_error"
    retryable = True


class AuthExpired(MailError):
    """Token revoked/expired and refresh failed: the user must reconnect."""

    code = "auth_expired"
    retryable = False


class RateLimited(MailError):
    code = "rate_limited"

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderUnavailable(MailError):
    code = "provider_unavailable"


class MailProvider(Protocol):
    def iter_pages(
        self, cursor: str | None, *, lookback_days: int, page_size: int
    ) -> Iterator[FetchPage]:
        """Yield pages; each page's ``next_cursor`` is safe to persist once stored."""
        ...
