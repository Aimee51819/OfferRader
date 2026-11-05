"""GET-only HTTP helper shared by the Gmail and Graph adapters.

This is the enforcement point for "never modify the mailbox": the helper has
no way to issue POST/PATCH/PUT/DELETE, and it refuses hosts other than the
provider API host it was created for.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from .base import AuthExpired, ProviderUnavailable, RateLimited


class ReadOnlyClient:
    def __init__(
        self,
        allowed_host: str,
        get_token: Callable[[], str],
        refresh_token: Callable[[], str],
        *,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 3,
    ) -> None:
        self.allowed_host = allowed_host
        self._get_token = get_token
        self._refresh = refresh_token
        self._client = httpx.Client(timeout=30, transport=transport)
        self.max_retries = max_retries

    def close(self) -> None:
        self._client.close()

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None) -> dict:
        host = urlsplit(url).hostname
        if host != self.allowed_host:
            raise ValueError(f"refusing request to unexpected host {host!r}")
        refreshed = False
        for attempt in range(self.max_retries + 1):
            hdrs = {"Authorization": f"Bearer {self._get_token()}", **(headers or {})}
            try:
                r = self._client.request("GET", url, params=params, headers=hdrs)
            except httpx.HTTPError as exc:
                if attempt >= self.max_retries:
                    raise ProviderUnavailable(f"network error: {type(exc).__name__}") from exc
                time.sleep(min(2**attempt, 8))
                continue
            if r.status_code == 401 and not refreshed:
                self._refresh()
                refreshed = True
                continue
            if r.status_code == 401:
                raise AuthExpired("provider rejected the access token after refresh")
            if r.status_code == 429 or (r.status_code == 403 and "rateLimit" in r.text):
                retry_after = _retry_after(r)
                if attempt >= self.max_retries:
                    raise RateLimited("provider rate limit", retry_after)
                time.sleep(min(retry_after or 2**attempt, 30))
                continue
            if r.status_code in (500, 502, 503, 504):
                if attempt >= self.max_retries:
                    raise ProviderUnavailable(f"provider HTTP {r.status_code}")
                time.sleep(min(_retry_after(r) or 2**attempt, 30))
                continue
            if r.status_code == 403:
                raise AuthExpired("permission denied (consent revoked or scope missing)")
            if r.status_code == 404:
                return {"__status__": 404}
            r.raise_for_status()
            return r.json()
        raise ProviderUnavailable("retries exhausted")


def _retry_after(r: httpx.Response) -> float | None:
    value = r.headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
