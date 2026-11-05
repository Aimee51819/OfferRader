"""Microsoft Graph read-only adapter (delegated Mail.Read). GET endpoints only.

Uses per-folder delta queries. Cursor (JSON):
  {"folders": {"inbox": {"next": url|None, "delta": url|None}}}
``next`` is persisted after every page; ``delta`` once a round completes, so
the next sync returns only changes since then. ``@removed`` entries are
reported, never acted on (evidence is not deleted).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import httpx

from .base import FetchPage, ProviderMessage
from .http_readonly import ReadOnlyClient

GRAPH = "https://graph.microsoft.com/v1.0"
_SELECT = (
    "id,conversationId,internetMessageId,subject,from,toRecipients,sentDateTime,"
    "receivedDateTime,body,webLink,parentFolderId"
)
_HEADERS = {
    # Graph converts HTML bodies to text server-side; we never render HTML.
    "Prefer": 'outlook.body-content-type="text", odata.maxpagesize=50',
}


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def message_from_api(m: dict, folder: str | None = None) -> ProviderMessage:
    sender = ((m.get("from") or {}).get("emailAddress")) or {}
    to = (m.get("toRecipients") or [{}])[0].get("emailAddress", {}) if m.get("toRecipients") else {}
    body = (m.get("body") or {}).get("content") or ""
    if (m.get("body") or {}).get("contentType", "text").lower() == "html":
        from bs4 import BeautifulSoup

        body = BeautifulSoup(body, "html.parser").get_text("\n", strip=True)
    return ProviderMessage(
        provider_message_id=m["id"],
        thread_id=m.get("conversationId"),
        internet_message_id=m.get("internetMessageId"),
        sender_address=sender.get("address"),
        sender_name=sender.get("name"),
        recipient_address=to.get("address"),
        subject=m.get("subject") or "",
        sent_at=_dt(m.get("sentDateTime")),
        received_at=_dt(m.get("receivedDateTime")),
        body_text=body,
        web_link=m.get("webLink"),
        folder=folder,
    )


class OutlookProvider:
    def __init__(self, client: ReadOnlyClient, scope: dict) -> None:
        self.c = client
        self.scope = scope or {}
        self.folders: list[str] = list(self.scope.get("folders") or ["inbox"])

    def iter_pages(self, cursor: str | None, *, lookback_days: int, page_size: int = 50) -> Iterator[FetchPage]:
        state = json.loads(cursor) if cursor else {}
        folders = state.setdefault("folders", {})
        for folder in self.folders:
            fs = folders.setdefault(folder, {"next": None, "delta": None})
            url, params = fs.get("next") or fs.get("delta"), None
            if not url:
                since = (datetime.now(timezone.utc) - timedelta(days=max(1, lookback_days))).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                url = f"{GRAPH}/me/mailFolders/{folder}/messages/delta"
                params = {"$select": _SELECT, "$filter": f"receivedDateTime ge {since}"}
            while url:
                data = self.c.get(url, params=params, headers=_HEADERS)
                params = None
                if data.get("__status__") == 404:
                    fs.update({"next": None, "delta": None, "error": "folder_not_found"})
                    yield FetchPage([], json.dumps(state))
                    break
                msgs, removed = [], []
                for item in data.get("value", []):
                    if "@removed" in item:
                        removed.append(item.get("id"))
                    else:
                        msgs.append(message_from_api(item, folder))
                if data.get("@odata.nextLink"):
                    fs["next"] = url = data["@odata.nextLink"]
                else:
                    fs["next"] = None
                    fs["delta"] = data.get("@odata.deltaLink") or fs.get("delta")
                    url = None
                fs.pop("error", None)
                yield FetchPage(msgs, json.dumps(state), has_more=bool(url), removed_ids=removed)


def build(get_token, refresh_token, scope: dict, transport: httpx.BaseTransport | None = None) -> OutlookProvider:
    return OutlookProvider(ReadOnlyClient("graph.microsoft.com", get_token, refresh_token, transport=transport), scope)
