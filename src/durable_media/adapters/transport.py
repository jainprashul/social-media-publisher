from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Pattern, Protocol

from .base import (
    AdapterError,
    AuthenticationError,
    MalformedResponseError,
    RateLimitError,
    ServerError,
    TimeoutError,
    UnknownRemoteStateError,
    ValidationError,
)
from ..security import redact


@dataclass
class TransportResponse:
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    content: bytes = b""
    json_body: Any = None

    def __post_init__(self):
        # Normalize header keys to lowercase for case-insensitive lookup
        self.headers = {k.lower(): v for k, v in self.headers.items()}
        if self.json_body is not None:
            self.text = json.dumps(self.json_body)
            self.content = self.text.encode("utf-8")
            if "content-type" not in self.headers:
                self.headers["content-type"] = "application/json"

    def get_header(self, key: str, default: str | None = None) -> str | None:
        return self.headers.get(key.lower(), default)


    def json(self) -> Any:
        raw = self.text or (self.content.decode("utf-8") if self.content else "")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception as err:
            raise MalformedResponseError(f"Failed to parse JSON response: {err}") from err


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        data: bytes | str | None = None,
        json_body: Any = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        timeout: float = 30.0,
    ) -> TransportResponse:
        ...


def sanitize_headers(headers: dict[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    out = {}
    for k, v in headers.items():
        k_lower = k.lower()
        if any(s in k_lower for s in ("auth", "token", "key", "secret", "cookie", "sig")):
            out[k] = "[REDACTED]"
        else:
            out[k] = redact(v)
    return out


def sanitize_url(url: str) -> str:
    return redact(url)


def raise_for_status(response: TransportResponse, context: str = "") -> None:
    code = response.status_code
    # 2xx is success; 308 Resume Incomplete is valid YouTube chunk status
    if 200 <= code < 300 or code == 308:
        return

    body = response.text[:400] if response.text else ""
    ctx = f"{context}: " if context else ""
    msg = f"{ctx}HTTP {code} - {body}".strip(": ")

    # Provider specific error inspection if body contains json
    error_code = None
    try:
        data = response.json()
        if isinstance(data, dict):
            err_obj = data.get("error", {})
            if isinstance(err_obj, dict):
                error_code = err_obj.get("code")
                msg = f"{ctx}{err_obj.get('message', msg)}"
    except Exception:
        pass

    if code in (408, 504):
        raise TimeoutError(msg)
    if code == 429:
        raise RateLimitError(msg)
    if 500 <= code < 600:
        raise ServerError(msg)
    if code in (401, 403):
        # Meta auth error codes: 190
        raise AuthenticationError(msg)
    if code in (400, 404, 409, 413, 415, 422):
        raise ValidationError(msg)
    raise AdapterError(msg)


class FakeHttpTransport:
    """Deterministic offline transport with pattern matching and request recording."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self._handlers: list[tuple[str, re.Pattern, Callable[..., TransportResponse]]] = []

    def register_response(
        self,
        method: str,
        url_pattern: str | Pattern,
        status_code: int = 200,
        json_body: Any = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ):
        pattern = re.compile(url_pattern if isinstance(url_pattern, str) else url_pattern.pattern)
        hdrs = headers or {}
        if json_body is not None:
            raw_text = json.dumps(json_body)
            if "content-type" not in {k.lower() for k in hdrs}:
                hdrs["content-type"] = "application/json"
        else:
            raw_text = text

        def handler(req: dict[str, Any]) -> TransportResponse:
            return TransportResponse(
                status_code=status_code,
                headers=hdrs,
                text=raw_text,
                content=raw_text.encode("utf-8"),
            )

        self._handlers.append((method.upper(), pattern, handler))

    def register_handler(
        self,
        method: str,
        url_pattern: str | Pattern,
        handler_fn: Callable[[dict[str, Any]], TransportResponse],
    ):
        pattern = re.compile(url_pattern if isinstance(url_pattern, str) else url_pattern.pattern)
        self._handlers.append((method.upper(), pattern, handler_fn))

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        data: bytes | str | None = None,
        json_body: Any = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        timeout: float = 30.0,
    ) -> TransportResponse:
        from urllib.parse import unquote, urlencode
        full_url = url
        if params:
            sep = "&" if "?" in url else "?"
            full_url = f"{url}{sep}{urlencode(params)}"
        unquoted_url = unquote(full_url)

        call_record = {
            "method": method.upper(),
            "url": url,
            "full_url": full_url,
            "headers": headers or {},
            "params": params or {},
            "data": data,
            "json": json_body,
            "files": files,
            "timeout": timeout,
        }
        self.calls.append(call_record)

        for m, pat, fn in self._handlers:
            if m == method.upper() and (pat.search(url) or pat.search(full_url) or pat.search(unquoted_url)):
                return fn(call_record)


        raise AdapterError(f"FakeHttpTransport: unhandled request {method.upper()} {url}")


    def get_sanitized_calls(self) -> list[dict[str, Any]]:
        return [
            {
                "method": c["method"],
                "url": sanitize_url(c["url"]),
                "headers": sanitize_headers(c["headers"]),
                "params": redact(c["params"]),
                "json": redact(c["json"]),
            }
            for c in self.calls
        ]
