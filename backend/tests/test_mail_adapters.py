"""Gmail / Graph adapters against mocked HTTP: read-only, paging, refresh, rate limits."""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.services.mail import gmail, oauth, outlook
from app.services.mail.base import AuthExpired


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _gmail_msg(mid: str, subject: str) -> dict:
    return {
        "id": mid, "threadId": "t" + mid, "internalDate": "1802534400000", "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "IBM Careers <ibm@myworkday.example>"},
                {"name": "To", "value": "aimee@example.edu"},
                {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": f"<{mid}@x>"},
                {"name": "Date", "value": "Mon, 1 Feb 2027 09:00:00 -0500"},
            ],
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64("<p>Thanks <script>alert(1)</script>for applying</p>")}},
                {"mimeType": "application/pdf", "filename": "offer.pdf", "body": {"attachmentId": "a1"}},
            ],
        },
    }


def test_gmail_backfill_then_history_get_only_with_refresh_and_429():
    seen: list[httpx.Request] = []
    state = {"rate_limited": False, "token": "old"}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert req.method == "GET"
        if req.headers["Authorization"] == "Bearer old":
            return httpx.Response(401)
        path = req.url.path
        if path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "aimee@example.edu", "historyId": "100"})
        if path.endswith("/messages") and "pageToken" not in req.url.params:
            return httpx.Response(200, json={"messages": [{"id": "m1"}], "nextPageToken": "p2"})
        if path.endswith("/messages"):
            if not state["rate_limited"]:
                state["rate_limited"] = True
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json={"messages": [{"id": "m2"}]})
        if "/messages/" in path:
            mid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json=_gmail_msg(mid, f"Subject {mid}"))
        if path.endswith("/history"):
            return httpx.Response(200, json={"history": [{"messagesAdded": [{"message": {"id": "m3"}}]}], "historyId": "120"})
        return httpx.Response(404)

    def refresh():
        state["token"] = "new"
        return "new"

    prov = gmail.build(lambda: state["token"], refresh, {}, transport=httpx.MockTransport(handler))
    pages = list(prov.iter_pages(None, lookback_days=30))
    ids = [m.provider_message_id for p in pages for m in p.messages]
    assert ids == ["m1", "m2", "m3"]
    assert json.loads(pages[-1].next_cursor)["history_id"] == "120"
    m1 = pages[0].messages[0]
    assert "alert" not in m1.body_text or "<script>" not in m1.body_text
    assert m1.sender_address == "ibm@myworkday.example" and m1.internet_message_id == "<m1@x>"
    assert all(r.method == "GET" for r in seen)


def test_gmail_history_expired_falls_back_to_backfill():
    def handler(req):
        if req.url.path.endswith("/history"):
            return httpx.Response(404)
        if req.url.path.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "5"})
        if req.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": []})
        return httpx.Response(404)

    prov = gmail.build(lambda: "t", lambda: "t", {}, transport=httpx.MockTransport(handler))
    cursor = json.dumps({"history_id": "1", "backfill_done": True})
    pages = list(prov.iter_pages(cursor, lookback_days=7))
    assert json.loads(pages[-1].next_cursor)["backfill_done"] is True


def test_graph_delta_paging_and_removed():
    def handler(req):
        assert req.method == "GET"
        assert 'outlook.body-content-type="text"' in req.headers["Prefer"]
        if "page2" in str(req.url):
            return httpx.Response(200, json={"value": [{"id": "gone", "@removed": {"reason": "deleted"}}],
                                             "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=d1"})
        return httpx.Response(200, json={
            "value": [{"id": "o1", "conversationId": "c1", "internetMessageId": "<o1@x>", "subject": "Interview",
                       "from": {"emailAddress": {"name": "Globex", "address": "careers@globex.example"}},
                       "receivedDateTime": "2027-03-22T15:00:00Z", "body": {"contentType": "text", "content": "hello"}}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/next?page2=1"})

    prov = outlook.build(lambda: "t", lambda: "t", {"folders": ["inbox"]}, transport=httpx.MockTransport(handler))
    pages = list(prov.iter_pages(None, lookback_days=10))
    assert [m.provider_message_id for p in pages for m in p.messages] == ["o1"]
    assert pages[1].removed_ids == ["gone"]
    assert json.loads(pages[-1].next_cursor)["folders"]["inbox"]["delta"].endswith("token=d1")


def test_readonly_client_refuses_other_hosts_and_reports_revoked_consent():
    prov = gmail.build(lambda: "t", lambda: "t", {}, transport=httpx.MockTransport(lambda r: httpx.Response(403, text="forbidden")))
    with pytest.raises(ValueError):
        prov.c.get("https://evil.example/steal")
    with pytest.raises(AuthExpired):
        prov.c.get(f"{gmail.API}/profile")


def test_oauth_never_requests_write_scopes(monkeypatch):
    monkeypatch.setattr(oauth.settings, "google_client_id", "cid")
    monkeypatch.setattr(oauth.settings, "google_client_secret", "sec")
    monkeypatch.setattr(oauth.settings, "microsoft_client_id", "mid")
    g = oauth.start("gmail")
    m = oauth.start("outlook")
    assert "gmail.readonly" in g and "code_challenge_method=S256" in g
    assert "Mail.Read" in m and "Mail.ReadWrite" not in m and "Mail.Send" not in m
    with pytest.raises(ValueError):
        oauth._assert_read_only(["https://www.googleapis.com/auth/gmail.modify"])
    assert "localhost" in oauth.redirect_uri("outlook")


def test_imap_uses_examine_and_peek():
    import inspect

    from app.services.mail import imap_client

    src = inspect.getsource(imap_client.ImapProvider.iter_pages)
    assert "readonly=True" in src and "BODY.PEEK[]" in src and "STORE" not in src.upper().replace("RESTORE", "")
