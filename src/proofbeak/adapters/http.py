"""Bounded HTTP with no redirects, implicit proxy credentials, or automatic writes."""

import socket
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from proofbeak.adapters.base import ProviderError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: dict


class Transport:
    def __init__(self, timeout: int = 10, max_calls: int = 24, max_bytes: int = 1_048_576):
        self.timeout = timeout
        self.max_calls = max_calls
        self.max_bytes = max_bytes
        self.calls = 0
        self.total_calls = 0
        self.opener = build_opener(NoRedirect(), ProxyHandler({}))

    def request(self, method, url, *, body=None, headers=None, allowed=(200, 201, 204)):
        if self.calls >= self.max_calls:
            raise ProviderError("request_budget_exceeded")
        self.calls += 1
        self.total_calls += 1
        req = Request(url, data=body, method=method, headers=headers or {})
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                content = response.read(self.max_bytes + 1)
                if len(content) > self.max_bytes:
                    raise ProviderError("response_too_large")
                if response.status not in allowed:
                    raise ProviderError("unexpected_http_status")
                return Response(response.status, content,
                                {k.lower(): v for k, v in response.headers.items()})
        except HTTPError as exc:
            exc.close()
            if exc.code in allowed:
                return Response(exc.code, b"", {})
            reason = {401: "permission_denied", 403: "permission_denied",
                      405: "resource_conflict", 409: "resource_conflict",
                      429: "rate_limited", 412: "concurrent_modification"}.get(
                          exc.code, "provider_http_error")
            raise ProviderError(reason) from None
        except (TimeoutError, socket.timeout, URLError, OSError):
            reason = "write_outcome_unknown" if method not in {"GET", "REPORT", "PROPFIND"} \
                else "observation_unavailable"
            raise ProviderError(reason) from None
