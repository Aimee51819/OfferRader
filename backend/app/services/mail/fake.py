"""Fixture-backed provider for tests and the synthetic demo. No network."""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ...config import settings
from .base import FetchPage, ProviderMessage

_REPO_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


def _allowed(path: Path) -> bool:
    path = path.resolve()
    return any(str(path).startswith(str(root.resolve())) for root in (_REPO_FIXTURES, settings.data_path))


def load_fixture_messages(directory: str | Path) -> list[dict]:
    d = Path(directory)
    if not _allowed(d):
        raise ValueError("fake provider may only read the repo's synthetic fixtures or DATA_DIR")
    items = []
    for f in sorted(d.glob("*.json")):
        items.append(json.loads(f.read_text(encoding="utf-8")))
    return items


def to_provider_message(item: dict) -> ProviderMessage:
    def dt(v):
        return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None

    return ProviderMessage(
        provider_message_id=item["provider_message_id"],
        thread_id=item.get("thread_id"),
        internet_message_id=item.get("internet_message_id"),
        sender_address=item.get("from_address"),
        sender_name=item.get("from_name"),
        recipient_address=item.get("to", "aimee.test@example.edu"),
        subject=item["subject"],
        sent_at=dt(item.get("sent_at") or item.get("received_at")),
        received_at=dt(item.get("received_at")),
        body_text=item["body"],
    )


class FakeProvider:
    """Serves fixture messages in received order; cursor = last served index."""

    def __init__(self, items: list[dict], page_size: int = 5, fail_after_pages: int | None = None) -> None:
        self.items = sorted(items, key=lambda i: i.get("received_at") or "")
        self.page_size = page_size
        self.fail_after_pages = fail_after_pages

    def iter_pages(self, cursor: str | None, *, lookback_days: int, page_size: int = 50) -> Iterator[FetchPage]:
        start = int(json.loads(cursor)["offset"]) if cursor else 0
        pages = 0
        while start < len(self.items):
            if self.fail_after_pages is not None and pages >= self.fail_after_pages:
                from .base import ProviderUnavailable

                raise ProviderUnavailable("simulated network interruption")
            chunk = self.items[start : start + self.page_size]
            start += len(chunk)
            pages += 1
            yield FetchPage(
                [to_provider_message(i) for i in chunk],
                json.dumps({"offset": start}),
                has_more=start < len(self.items),
            )
