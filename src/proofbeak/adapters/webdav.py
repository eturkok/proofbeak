"""Owned disposable WebDAV collections; independent listings and GETs observe storage."""

import base64
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from urllib.parse import quote, unquote, urljoin, urlsplit

from proofbeak.adapters.base import ProviderError, Resource
from proofbeak.adapters.http import Transport
from proofbeak.models import fingerprint
from proofbeak.packs.storage import StorageSnapshot, content_hash

OWNER = ".proofbeak-owner"
PROPFIND = (b'<D:propfind xmlns:D="DAV:"><D:prop><D:resourcetype/>'
            b'</D:prop></D:propfind>')


class WebDAVAdapter:
    version = "0.1.0"
    evidence_level = "provider_test_account"
    supported_trials = frozenset({"storage.put_same_key.v1"})

    def __init__(self, name, endpoint, username, password, transport=None):
        if not isinstance(name, str) or not re.fullmatch(r"webdav:[a-z][a-z0-9_-]{0,31}", name):
            raise ValueError("WebDAV candidate must be webdav:<operator-alias>")
        url = urlsplit(endpoint)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password \
                or url.query or url.fragment or url.path not in {"", "/"}:
            raise ValueError("WebDAV endpoint must be an origin without credentials")
        if url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("remote WebDAV endpoints require HTTPS")
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", username) \
                or not isinstance(password, str) or not 1 <= len(password) <= 8192:
            raise ValueError("configure a dedicated WebDAV test username and password")
        self.name, self.endpoint = name, endpoint.rstrip("/")
        self.username, self._password = username, password
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.headers = {"Authorization": "Basic " + token}
        self.transport = transport or Transport()
        if transport is not None:
            self.evidence_level = "simulation"
        self.profile = {"endpoint": self.endpoint, "account_scope": fingerprint(username),
                        "provider_version": "unknown", "permissions": "owned_trial_collections",
                        "transport": "injected" if transport is not None else url.scheme}
        self.created, self.etags = set(), {}

    def resource(self, run_id):
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("invalid run id")
        return Resource(f"{self.endpoint}/proofbeak-{run_id}/", run_id)

    def _request(self, resource, method, *, key=None, body=None, headers=None, allowed=(200,)):
        if resource != self.resource(resource.scope_id):
            raise ProviderError("resource_scope_violation")
        if key is not None and (key in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key)):
            raise ProviderError("resource_scope_violation")
        url = resource.id + (quote(key, safe="") if key is not None else "")
        return self.transport.request(method, url, body=body,
            headers={**self.headers, **(headers or {})}, allowed=allowed)

    @staticmethod
    def _marker(resource):
        return f"proofbeak:owned-storage-trial:{resource.scope_id}".encode()

    def _owned(self, resource, *, allow_missing_created=False):
        response = self._request(resource, "GET", key=OWNER, allowed=(200, 404))
        if response.status == 404 and allow_missing_created and resource.scope_id in self.created:
            return
        if response.status != 200 or response.body != self._marker(resource):
            raise ProviderError("resource_ownership_unverified")

    def provision(self, resource, trial):
        self._request(resource, "MKCOL", allowed=(201,))
        self.created.add(resource.scope_id)
        self._request(resource, "PUT", key=OWNER, body=self._marker(resource),
                      headers={"If-None-Match": "*", "Content-Type": "text/plain"}, allowed=(201,))

    def seed(self, resource, trial):
        self._owned(resource)
        self._request(resource, "PUT", key=trial.key, body=trial.content.encode(),
                      headers={"If-None-Match": "*", "Content-Type": "application/octet-stream"},
                      allowed=(201,))

    def _listing(self, resource):
        response = self._request(resource, "PROPFIND", body=PROPFIND,
            headers={"Depth": "1", "Content-Type": "application/xml"}, allowed=(207,))
        try:
            if b"<!DOCTYPE" in response.body.upper() or b"<!ENTITY" in response.body.upper():
                raise ValueError("XML declarations are not allowed")
            document = ET.fromstring(response.body)
            entries = document.findall("{DAV:}response")
            if document.tag != "{DAV:}multistatus" or not 1 <= len(entries) <= 102:
                raise ValueError("invalid multistatus")
            names, seen, root_seen = set(), set(), False
            origin = urlsplit(self.endpoint)
            base = urlsplit(resource.id).path
            for entry in entries:
                href = entry.findtext("{DAV:}href")
                if not isinstance(href, str) or not href:
                    raise ValueError("missing href")
                target = urlsplit(urljoin(resource.id, href))
                if (target.scheme, target.netloc) != (origin.scheme, origin.netloc) \
                        or target.query or target.fragment:
                    raise ValueError("foreign href")
                path = unquote(target.path, errors="strict")
                if path in seen:
                    raise ValueError("duplicate href")
                seen.add(path)
                props = entry.findall("{DAV:}propstat")
                if entry.find("{DAV:}status") is not None or not props \
                        or any(" 200 " not in p.findtext("{DAV:}status", "") for p in props):
                    raise ValueError("unreadable properties")
                collection = any(p.find("{DAV:}prop/{DAV:}resourcetype/{DAV:}collection") is not None
                                 for p in props)
                if path == base and collection:
                    root_seen = True
                    continue
                if not path.startswith(base) or collection:
                    raise ValueError("out-of-scope or nested collection")
                name = path[len(base):]
                if name in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
                    raise ValueError("invalid member name")
                names.add(name)
            if not root_seen or OWNER not in names:
                raise ValueError("incomplete listing")
            return names - {OWNER}
        except (ValueError, ET.ParseError, UnicodeError):
            raise ProviderError("invalid_observation") from None

    def read(self, resource, trial):
        self._owned(resource)
        names = self._listing(resource)
        objects = {}
        for name in names | {trial.key}:
            response = self._request(resource, "GET", key=name, allowed=(200, 404))
            if name not in names:
                if response.status == 200:
                    raise ProviderError("invalid_observation")
                continue
            if response.status != 200:
                raise ProviderError("observation_unavailable")
            if len(response.body) > 4096:
                raise ProviderError("invalid_observation")
            objects[name] = content_hash(response.body)
            if name == trial.key:
                self.etags[resource.scope_id] = response.headers.get("etag")
        return StorageSnapshot(objects, "webdav:propfind-and-independent-get", datetime.now(UTC).isoformat())

    def apply(self, resource, trial):
        self._owned(resource)
        etag = self.etags.get(resource.scope_id)
        if not isinstance(etag, str) or not re.fullmatch(r'"[^"\x00-\x1f\x7f]{1,256}"', etag):
            raise ProviderError("etag_unavailable")
        self._request(resource, "PUT", key=trial.key, body=trial.content.encode(),
            headers={"If-Match": etag, "Content-Type": "application/octet-stream"},
            allowed=(200, 201, 204))

    def cleanup(self, resource):
        self.transport.calls = 0
        response = self._request(resource, "PROPFIND", body=PROPFIND,
            headers={"Depth": "0", "Content-Type": "application/xml"}, allowed=(207, 404))
        if response.status == 404:
            return
        self._owned(resource, allow_missing_created=True)
        self._request(resource, "DELETE", allowed=(200, 204, 404))
        response = self._request(resource, "PROPFIND", body=PROPFIND,
            headers={"Depth": "0", "Content-Type": "application/xml"}, allowed=(207, 404))
        if response.status != 404:
            raise ProviderError("cleanup_unavailable")
