"""Gmail read-only adapter (scope gmail.readonly). Uses GET endpoints only.

Cursor (JSON):
  {"history_id": "...", "backfill_token": "...", "backfill_done": bool}
Backfill lists messages newer than the lookback window page by page; the cursor
is persisted after each page, so a crash or sleep resumes where it stopped.
Afterwards the History API returns only messages added since ``history_id``.
If Gmail expired that history id (HTTP 404), we fall back to a dated re-list;
duplicates are harmless because ingestion is idempotent.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

import httpx
from bs4 import BeautifulSoup

from .base import FetchPage, ProviderMessage
from .http_readonly import ReadOnlyClient

API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _b64(data: str) -> str:
    data += "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")


def _walk(part: dict, out: dict) -> None:
    mime = part.get("mimeType", "")
    if part.get("filename"):
        return  # attachments are never processed
    data = (part.get("body") or {}).get("data")
    if data and mime == "text/plain" and "plain" not in out:
        out["plain"] = _b64(data)
    elif data and mime == "text/html" and "html" not in out:
        out["html"] = _b64(data)
    for child in part.get("parts") or []:
        _walk(child, out)


def message_from_api(m: dict) -> ProviderMessage:
    payload = m.get("payload") or {}
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    bodies: dict = {}
    _walk(payload, bodies)
    text = bodies.get("plain")
    if not text and bodies.get("html"):
        # Converted to text only; HTML is never rendered or stored.
        text = BeautifulSoup(bodies["html"], "html.parser").get_text("\n", strip=True)
    name, addr = parseaddr(headers.get("from", ""))
    to = getaddresses([headers.get("to", "")])
    sent = None
    if headers.get("date"):
        try:
            sent = parsedate_to_datetime(headers["date"]).astimezone(timezone.utc)
        except (TypeError, ValueError):
            sent = None
    received = None
    if m.get("internalDate"):
        received = datetime.fromtimestamp(int(m["internalDate"]) / 1000, tz=timezone.utc)
    return ProviderMessage(
        provider_message_id=m["id"],
        thread_id=m.get("threadId"),
        internet_message_id=headers.get("message-id"),
        sender_address=addr or None,
        sender_name=name or None,
        recipient_address=to[0][1] if to else None,
        subject=headers.get("subject", ""),
        sent_at=sent,
        received_at=received or sent,
        body_text=text or m.get("snippet", ""),
        web_link=f"https://mail.google.com/mail/u/0/#all/{m['id']}",
        folder=",".join(m.get("labelIds") or []),
    )


class GmailProvider:
    def __init__(self, client: ReadOnlyClient, scope: dict) -> None:
        self.c = client
        self.scope = scope or {}

    def address(self) -> tuple[str, str]:
        prof = self.c.get(f"{API}/profile")
        return prof.get("emailAddress", ""), str(prof.get("historyId", ""))

    def _list_params(self, lookback_days: int, page_token: str | None, page_size: int) -> dict:
        q = [f"newer_than:{max(1, lookback_days)}d"]
        params: dict = {"maxResults": page_size, "q": " ".join(q)}
        if self.scope.get("inbox_only"):
            params["labelIds"] = "INBOX"
        if self.scope.get("include_spam"):
            params["includeSpamTrash"] = "true"
            params["q"] += " -in:trash"
        if page_token:
            params["pageToken"] = page_token
        return params

    def _get_full(self, mid: str) -> ProviderMessage | None:
        data = self.c.get(f"{API}/messages/{mid}", params={"format": "full"})
        if data.get("__status__") == 404:
            return None  # deleted after listing: nothing to ingest
        if "SPAM" in (data.get("labelIds") or []) and not self.scope.get("include_spam"):
            return None
        return message_from_api(data)

    def iter_pages(
        self, cursor: str | None, *, lookback_days: int, page_size: int = 50, _history: bool = True
    ) -> Iterator[FetchPage]:
        state = json.loads(cursor) if cursor else {}
        if not state.get("history_id"):
            _addr, hid = self.address()
            state["history_id"] = hid  # changes during backfill are caught by history later
        while not state.get("backfill_done"):
            data = self.c.get(
                f"{API}/messages", params=self._list_params(lookback_days, state.get("backfill_token"), page_size)
            )
            msgs = [m for m in (self._get_full(x["id"]) for x in data.get("messages", [])) if m]
            token = data.get("nextPageToken")
            state["backfill_token"] = token
            state["backfill_done"] = token is None
            yield FetchPage(msgs, json.dumps(state), has_more=True)
        while _history:
            params = {
                "startHistoryId": state["history_id"],
                "historyTypes": "messageAdded",
                "maxResults": page_size,
            }
            if state.get("history_token"):
                params["pageToken"] = state["history_token"]
            data = self.c.get(f"{API}/history", params=params)
            if data.get("__status__") == 404:
                # History id expired (long sleep): re-run a dated backfill; ingestion dedups.
                state = {"backfill_done": False}
                yield FetchPage([], json.dumps(state), has_more=True)
                yield from self.iter_pages(
                    json.dumps(state), lookback_days=lookback_days, page_size=page_size, _history=False
                )
                return
            ids: list[str] = []
            for h in data.get("history", []):
                for added in h.get("messagesAdded", []):
                    mid = added.get("message", {}).get("id")
                    if mid and mid not in ids:
                        ids.append(mid)
            msgs = [m for m in (self._get_full(i) for i in ids) if m]
            token = data.get("nextPageToken")
            if token:
                state["history_token"] = token
            else:
                state.pop("history_token", None)
                if data.get("historyId"):
                    state["history_id"] = str(data["historyId"])
            yield FetchPage(msgs, json.dumps(state), has_more=bool(token))
            if not token:
                return


def build(get_token, refresh_token, scope: dict, transport: httpx.BaseTransport | None = None) -> GmailProvider:
    return GmailProvider(ReadOnlyClient("gmail.googleapis.com", get_token, refresh_token, transport=transport), scope)
