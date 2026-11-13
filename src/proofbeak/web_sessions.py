"""Ephemeral browser sessions; bearer keys never enter URLs or browser storage."""

import hashlib
import hmac
import secrets
import time
from threading import Lock

from proofbeak.service import ServiceError

COOKIE = "proofbeak_session"
LIFETIME = 8 * 60 * 60


def same_origin(request):
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise ServiceError("cross_origin_request", 403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise ServiceError("cross_origin_request", 403)


class BrowserSessions:
    def __init__(self, service):
        self.service = service
        self.sessions = {}
        self.lock = Lock()

    def create(self, token, request):
        same_origin(request)
        owner = self.service.authenticate(token)
        session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = time.monotonic()
        with self.lock:
            self.sessions = {key: value for key, value in self.sessions.items() if value["expires"] > now}
            if len(self.sessions) >= 256:
                raise ServiceError("session_limit_reached", 429)
            self.sessions[session] = {"owner": owner, "digest": hashlib.sha256(token.encode()).hexdigest(),
                                      "csrf": csrf, "expires": now + LIFETIME}
        return session, {"authenticated": True, "tenant": owner, "csrf": csrf, "expires_in": LIFETIME}

    def get(self, request, require=False):
        cookie = request.cookies.get(COOKIE, "")
        with self.lock:
            value = self.sessions.get(cookie)
            if value and value["expires"] <= time.monotonic():
                self.sessions.pop(cookie, None)
                value = None
        if value:
            tenant = self.service.tenants.get(value["owner"])
            if tenant is None or not hmac.compare_digest(tenant.token_sha256, value["digest"]) or self.service._disabled(value["digest"]):
                self.discard(request)
                value = None
        if value is None and require:
            raise ServiceError("unauthorized", 401)
        return value

    def authenticate(self, request):
        session = self.get(request, require=True)
        same_origin(request)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            csrf = request.headers.get("x-proofbeak-csrf", "")
            if not hmac.compare_digest(csrf, session["csrf"]):
                raise ServiceError("csrf_required", 403)
        return session["owner"]

    def discard(self, request):
        with self.lock:
            self.sessions.pop(request.cookies.get(COOKIE, ""), None)
