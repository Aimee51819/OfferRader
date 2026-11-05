"""Optional IMAP adapter (read-only). Prefer the Gmail / Graph OAuth adapters.

* Mailboxes are opened with EXAMINE (``select(readonly=True)``) and bodies are
  fetched with ``BODY.PEEK[]`` so the \\Seen flag is never set.
* Basic password auth only: Outlook.com / Microsoft 365 have disabled basic
  auth for IMAP, so use the Microsoft Graph adapter there. Gmail requires an
  app password (2-Step Verification) — still prefer the Gmail OAuth adapter.

Cursor (JSON): {"<folder>": {"uidvalidity": int, "last_uid": int}}
"""
from __future__ import annotations

import email
import imaplib
import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from bs4 import BeautifulSoup

from .base import AuthExpired, FetchPage, ProviderMessage, ProviderUnavailable


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def extract_body(msg: email.message.Message) -> str:
    plain: str | None = None
    html: str | None = None
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.is_multipart():
            continue
        if "attachment" in str(part.get("Content-Disposition") or ""):
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:  # noqa: BLE001
            continue
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and html is None:
            html = text
    if plain:
        return plain.strip()
    if html:
        return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    return ""


def parse_rfc822(raw: bytes, uid: str, folder: str) -> ProviderMessage:
    msg = email.message_from_bytes(raw)
    sent = None
    if msg.get("Date"):
        try:
            sent = parsedate_to_datetime(msg["Date"]).astimezone(timezone.utc)
        except (TypeError, ValueError):
            sent = None
    name, addr = parseaddr(_decode(msg.get("From")))
    to = getaddresses([_decode(msg.get("To"))])
    return ProviderMessage(
        provider_message_id=f"{folder}:{uid}",
        thread_id=None,
        internet_message_id=_decode(msg.get("Message-ID")) or None,
        sender_address=addr or None,
        sender_name=name or None,
        recipient_address=to[0][1] if to else None,
        subject=_decode(msg.get("Subject")),
        sent_at=sent,
        received_at=sent,
        body_text=extract_body(msg),
        folder=folder,
    )


class ImapProvider:
    def __init__(self, host: str, port: int, username: str, password: str, use_ssl: bool, scope: dict) -> None:
        self.host, self.port, self.username, self.password, self.use_ssl = host, port, username, password, use_ssl
        self.folders = list((scope or {}).get("folders") or ["INBOX"])

    def _connect(self) -> imaplib.IMAP4:
        try:
            conn: imaplib.IMAP4 = (
                imaplib.IMAP4_SSL(self.host, self.port, timeout=30)
                if self.use_ssl
                else imaplib.IMAP4(self.host, self.port, timeout=30)
            )
        except OSError as exc:
            raise ProviderUnavailable(f"IMAP connect failed: {type(exc).__name__}") from exc
        try:
            conn.login(self.username, self.password)
        except imaplib.IMAP4.error as exc:
            raise AuthExpired("IMAP login rejected (basic auth may be disabled by the provider)") from exc
        return conn

    def iter_pages(self, cursor: str | None, *, lookback_days: int, page_size: int = 50) -> Iterator[FetchPage]:
        state = json.loads(cursor) if cursor else {}
        conn = self._connect()
        try:
            for folder in self.folders:
                status, data = conn.select(f'"{folder}"', readonly=True)  # EXAMINE
                if status != "OK":
                    continue
                uidvalidity = None
                resp = conn.response("UIDVALIDITY")
                if resp and resp[1] and resp[1][0]:
                    try:
                        uidvalidity = int(resp[1][0])
                    except (TypeError, ValueError):
                        uidvalidity = None
                fs = state.get(folder) or {}
                if fs.get("uidvalidity") != uidvalidity:
                    fs = {"uidvalidity": uidvalidity, "last_uid": None}
                if fs.get("last_uid"):
                    criteria = f"UID {int(fs['last_uid']) + 1}:*"
                else:
                    since = (datetime.now(timezone.utc) - timedelta(days=max(1, lookback_days))).strftime("%d-%b-%Y")
                    criteria = f"SINCE {since}"
                status, found = conn.uid("search", None, criteria)
                if status != "OK" or not found or not found[0]:
                    state[folder] = fs
                    yield FetchPage([], json.dumps(state))
                    continue
                uids = sorted(int(x) for x in found[0].split())
                uids = [u for u in uids if not fs.get("last_uid") or u > int(fs["last_uid"])]
                for i in range(0, len(uids), page_size):
                    chunk = uids[i : i + page_size]
                    msgs = []
                    for uid in chunk:
                        status, msg_data = conn.uid("fetch", str(uid), "(BODY.PEEK[])")
                        if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                            continue
                        msgs.append(parse_rfc822(msg_data[0][1], str(uid), folder))
                    fs["last_uid"] = chunk[-1]
                    state[folder] = fs
                    yield FetchPage(msgs, json.dumps(state), has_more=i + page_size < len(uids))
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass
