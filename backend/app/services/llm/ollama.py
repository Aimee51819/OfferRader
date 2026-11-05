"""Minimal client for a *local* Ollama server. There is no remote fallback.

The base URL must resolve to a loopback host unless ALLOW_NON_LOOPBACK=true is
set explicitly, so email text is never shipped to a remote inference service
by accident.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import socket
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from ...config import settings

logger = logging.getLogger("tracker.ollama")

RECOMMENDED_TAGS = (
    "qwen3:4b-instruct-2507-q4_K_M",
    "qwen3:4b-instruct-2507-q8_0",
    "qwen3:4b-instruct-2507-fp16",
    "qwen3:4b-instruct",
    "qwen3:4b",
)

# Deterministic decoding. Recorded in parsed_messages.model_parameters.
MODEL_OPTIONS: dict[str, Any] = {
    "temperature": 0,
    "top_p": 1,
    "top_k": 1,
    "seed": 7,
    "num_ctx": 8192,
    "num_predict": 1200,
}


class LLMError(Exception):
    code = "llm_error"


class LLMUnavailable(LLMError):
    code = "llm_unavailable"


class LLMModelMissing(LLMError):
    code = "llm_model_missing"


class LLMTimeout(LLMError):
    code = "llm_timeout"


class LLMBadResponse(LLMError):
    code = "llm_bad_response"


class LLMBackend(Protocol):
    name: str
    parameters: dict

    def chat_json(self, system: str, user: str, schema: dict) -> str: ...


def assert_local_url(url: str) -> None:
    host = urlsplit(url).hostname or ""
    if settings.allow_non_loopback:
        return
    if host in {"localhost"}:
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
        if infos and all(ipaddress.ip_address(i[4][0]).is_loopback for i in infos):
            return
    except OSError:
        pass
    raise LLMError(
        f"Ollama URL host '{host}' is not loopback. Email content is only sent to a local "
        "model; set ALLOW_NON_LOOPBACK=true only after a security review."
    )


@dataclass
class OllamaBackend:
    base_url: str
    model: str
    timeout: float = 180.0
    max_retries: int = 2
    name: str = ""

    def __post_init__(self) -> None:
        assert_local_url(self.base_url)
        self.base_url = self.base_url.rstrip("/")
        self.name = f"ollama:{self.model}"
        self.parameters = dict(MODEL_OPTIONS)

    def _client(self) -> httpx.Client:
        # trust_env=False: never route local model traffic through an HTTP proxy.
        return httpx.Client(timeout=self.timeout, trust_env=False)

    def installed_models(self) -> list[str]:
        try:
            with self._client() as c:
                r = c.get(f"{self.base_url}/api/tags", timeout=5)
                r.raise_for_status()
                return [m.get("name") or m.get("model") for m in r.json().get("models", [])]
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Ollama is not reachable at {self.base_url}: {exc}") from exc

    def health(self) -> dict:
        try:
            with self._client() as c:
                version = c.get(f"{self.base_url}/api/version", timeout=5).json().get("version")
            models = self.installed_models()
        except (LLMError, httpx.HTTPError, ValueError) as exc:
            return {
                "ok": False,
                "reachable": False,
                "model": self.model,
                "error": str(exc),
                "hint": "Install Ollama (https://ollama.com/download), start it, then run "
                f"`ollama pull {self.model}`.",
            }
        present = self.model in models or f"{self.model}:latest" in models
        qwen = [m for m in models if m and m.startswith("qwen3")]
        return {
            "ok": present,
            "reachable": True,
            "version": version,
            "model": self.model,
            "model_installed": present,
            "installed_qwen3_models": qwen,
            "hint": None
            if present
            else f"Model '{self.model}' is not installed. Run `ollama pull {self.model}` or set "
            f"OLLAMA_MODEL to one of: {', '.join(qwen) or '(no qwen3 models installed)'}.",
        }

    def chat_json(self, system: str, user: str, schema: dict) -> str:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": schema,  # structured outputs (Ollama >= 0.5)
            "stream": False,
            "keep_alive": "10m",
            "options": self.parameters,
        }
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._client() as c:
                    r = c.post(f"{self.base_url}/api/chat", json=body)
                if r.status_code == 404 and "not found" in r.text.lower():
                    raise LLMModelMissing(
                        f"Model '{self.model}' is not installed in Ollama. "
                        f"Run `ollama pull {self.model}`."
                    )
                r.raise_for_status()
                data = r.json()
                content = (data.get("message") or {}).get("content")
                if not isinstance(content, str):
                    raise LLMBadResponse("Ollama response had no message content.")
                return content
            except LLMModelMissing:
                raise
            except httpx.TimeoutException as exc:
                last_exc = LLMTimeout(f"Ollama timed out after {self.timeout}s")
                logger.warning("Ollama timeout (attempt %s)", attempt + 1)
            except httpx.HTTPStatusError as exc:
                last_exc = LLMBadResponse(f"Ollama HTTP {exc.response.status_code}")
            except httpx.HTTPError as exc:
                last_exc = LLMUnavailable(f"Ollama is not reachable at {self.base_url}: {exc}")
            except (json.JSONDecodeError, ValueError) as exc:
                last_exc = LLMBadResponse(f"Ollama returned non-JSON: {exc}")
            if attempt < self.max_retries:
                time.sleep(min(2**attempt, 8))
        assert last_exc is not None
        raise last_exc


def default_backend(model: str | None = None, base_url: str | None = None) -> OllamaBackend:
    return OllamaBackend(
        base_url=base_url or settings.ollama_base_url,
        model=model or settings.ollama_model,
        timeout=settings.ollama_timeout_seconds,
        max_retries=settings.ollama_max_retries,
    )
