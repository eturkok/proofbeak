"""Injected protocol controls are simulations, not Apache/WsgiDAV observations."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from proofbeak.adapters.base import ProviderError, Resource
from proofbeak.adapters.http import Response
from proofbeak.adapters.webdav import OWNER, WebDAVAdapter
from proofbeak.cli import adapters_for
from proofbeak.engine import recover_cleanup, run_trial
from proofbeak.models import fingerprint
from proofbeak.server import provider_factory
from proofbeak.store import Store
from proofbeak.worker_adapters import decode_adapter, encode_adapter


class MemoryDAV:
    def __init__(self, mode="correct"):
        self.mode, self.exists, self.files = mode, False, {}
        self.calls = self.total_calls = self.counter = 0
        self.requests = []
        self.applied = False

    def request(self, method, url, *, body=None, headers=None, allowed=(200,)):
        self.calls += 1
        self.total_calls += 1
        self.requests.append((method, url))
        path = urlsplit(url).path
        key = path.rstrip("/").split("/")[-1]
        status, payload, response_headers = 200, b"", {}
        if method == "MKCOL":
            if self.mode == "permission":
                raise ProviderError("permission_denied")
            if self.exists:
                raise ProviderError("resource_conflict")
            self.exists = True
            self.base = path
            status = 201
        elif not self.exists:
            status = 404
        elif method == "PUT":
            if headers.get("If-None-Match") == "*" and key in self.files:
                raise ProviderError("concurrent_modification")
            repeat = "If-Match" in headers
            if repeat and self.mode == "concurrent":
                self.counter += 1
            if repeat and headers["If-Match"] not in {"*", f'"etag-{self.counter}"'}:
                raise ProviderError("concurrent_modification")
            status = 204 if key in self.files else 201
            self.files[key] = body
            self.counter += 1
            if repeat:
                self.applied = True
                if self.mode == "duplicate":
                    self.files[key + "-copy"] = body
                elif self.mode == "corrupt":
                    self.files[key] = b"changed"
                elif self.mode == "missing_target":
                    del self.files[key]
                elif self.mode == "owner_replaced":
                    self.files[OWNER] = b"someone-else"
                elif self.mode == "lost_write":
                    raise ProviderError("write_outcome_unknown")
        elif method == "GET":
            if key not in self.files or (self.mode == "unreadable_after" and self.applied and key != OWNER):
                status = 404
            else:
                payload = self.files[key]
                if self.mode != "missing_etag":
                    response_headers["etag"] = f'"etag-{self.counter}"'
                    if self.mode == "weak_etag":
                        response_headers["etag"] = "W/" + response_headers["etag"]
                    elif self.mode == "malformed_etag":
                        response_headers["etag"] = "not-a-validator"
        elif method == "PROPFIND":
            status = 207
            entries = [(self.base, True)]
            if headers["Depth"] == "1":
                for name in self.files:
                    if (self.mode == "hidden_target" and name != OWNER) \
                            or (self.mode == "missing_marker" and name == OWNER):
                        continue
                    href = self.base + name
                    if name != OWNER and self.mode == "foreign_href":
                        href = "https://foreign.example/elsewhere"
                    if name != OWNER and self.mode == "encoded_traversal":
                        href = self.base + "%2e%2e"
                    entries.append((href, False))
                if self.mode == "duplicate_href":
                    entries.append(entries[-1])
                if self.mode == "nested_collection":
                    entries.append((self.base + "nested/", True))
            children = []
            for href, collection in entries:
                resource_type = "<D:collection/>" if collection else ""
                code = 403 if self.mode == "partial_properties" else 200
                children.append(f"<D:response><D:href>{escape(href)}</D:href><D:propstat>"
                    f"<D:prop><D:resourcetype>{resource_type}</D:resourcetype></D:prop>"
                    f"<D:status>HTTP/1.1 {code} status</D:status></D:propstat></D:response>")
            payload = ('<D:multistatus xmlns:D="DAV:">' + "".join(children) + "</D:multistatus>").encode()
            if self.mode == "entity_xml":
                payload = b'<!DOCTYPE x [<!ENTITY x "hidden">]>' + payload
        elif method == "DELETE":
            if self.mode == "cleanup_denied":
                raise ProviderError("permission_denied")
            if self.mode != "cleanup_still_exists":
                self.exists, self.files = False, {}
            status = 204
        if status not in allowed:
            raise ProviderError("provider_http_error")
        return Response(status, payload, response_headers)


class WebDAVTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "runs.sqlite3")
        self.addCleanup(self.store.close)
        self.spec = json.loads(Path("examples/storage.put_same_key.v1.json").read_text())

    def adapter(self, mode="correct"):
        transport = MemoryDAV(mode)
        adapter = WebDAVAdapter("webdav:test", "http://127.0.0.1:8080", "test", "private-password",
                                transport=transport)
        return adapter, transport

    def execute(self, mode):
        adapter, transport = self.adapter(mode)
        report = run_trial(self.spec, [adapter], self.store, mode)
        return report["candidates"][0], adapter, transport

    def test_repeat_uses_listing_and_independent_gets_then_removes_owned_collection(self):
        result, _, transport = self.execute("correct")
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["cleanup_status"], "cleaned")
        self.assertEqual(len(result["evidence"]["after"]["objects"]), 1)
        self.assertFalse(transport.exists)
        self.assertEqual(result["evidence_level"], "simulation")
        self.assertFalse(result["eligible_for_provider_comparison"])
        self.assertEqual(result["measurements"]["provider_http_attempts"], transport.total_calls)
        self.assertNotIn("private-password", json.dumps(result))
        self.assertTrue(any(method == "PROPFIND" for method, _ in transport.requests))

    def test_observed_duplicate_corruption_and_missing_object_are_failures(self):
        for mode in ("duplicate", "corrupt", "missing_target"):
            with self.subTest(mode=mode):
                result, _, _ = self.execute(mode)
                self.assertEqual(result["verdict"], "fail")
                self.assertEqual(result["cleanup_status"], "cleaned")

    def test_permission_ambiguous_write_invalid_etag_and_read_gap_are_inconclusive(self):
        for mode, code in (("permission", "permission_denied"), ("lost_write", "write_outcome_unknown"),
                           ("concurrent", "concurrent_modification"), ("malformed_etag", "invalid_etag"),
                           ("unreadable_after", "observation_unavailable")):
            with self.subTest(mode=mode):
                result, _, _ = self.execute(mode)
                self.assertEqual(result["verdict"], "inconclusive")
                self.assertEqual(result["reason_code"], code)
        result = self.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        self.assertEqual(result, 5)

    def test_missing_or_weak_etag_does_not_add_an_unrequested_compare_and_swap_condition(self):
        for mode in ("missing_etag", "weak_etag"):
            with self.subTest(mode=mode):
                result, _, _ = self.execute(mode)
                self.assertEqual(result["verdict"], "pass")
                self.assertEqual(result["cleanup_status"], "cleaned")
                self.assertEqual(result["profile"]["write_precondition"],
                                 "strong_etag_when_available_else_existing_object")

    def test_invalid_and_partial_listings_never_become_capability_failures(self):
        for mode in ("foreign_href", "encoded_traversal", "duplicate_href", "nested_collection",
                     "partial_properties", "entity_xml", "hidden_target", "missing_marker"):
            with self.subTest(mode=mode):
                result, _, transport = self.execute(mode)
                self.assertEqual(result["verdict"], "inconclusive")
                self.assertEqual(result["reason_code"], "invalid_observation")
                self.assertFalse(transport.applied)
                self.assertFalse(any(url.endswith("/..") for _, url in transport.requests))

    def test_replaced_owner_is_never_deleted_and_successful_delete_must_be_verified(self):
        for mode in ("owner_replaced", "cleanup_denied", "cleanup_still_exists"):
            with self.subTest(mode=mode):
                result, _, transport = self.execute(mode)
                self.assertEqual(result["cleanup_status"], "quarantined")
                self.assertTrue(transport.exists)
                if mode == "owner_replaced":
                    self.assertFalse(any(method == "DELETE" for method, _ in transport.requests))

    def test_existing_collection_conflict_never_grants_delete_permission(self):
        adapter, transport = self.adapter()
        transport.exists = True
        transport.files = {OWNER: b"not-owned"}
        report = run_trial(self.spec, [adapter], self.store, "conflict")
        self.assertEqual(report["candidates"][0]["cleanup_status"], "ownership_unverified")
        self.assertFalse(any(method == "DELETE" for method, _ in transport.requests))

    def test_recovery_verifies_durable_marker_in_a_fresh_adapter(self):
        adapter, transport = self.adapter()
        run_id, _ = self.store.reserve("interrupted", "fingerprint")
        resource = adapter.resource(run_id)
        self.store.track(run_id, adapter.name, fingerprint(adapter.profile),
                         {"id": resource.id, "scope_id": resource.scope_id})
        adapter.provision(resource, None)
        fresh = WebDAVAdapter(adapter.name, adapter.endpoint, adapter.username, "private-password",
                              transport=transport)
        self.assertEqual(recover_cleanup(self.store, [fresh])[0]["status"], "cleaned")
        self.assertFalse(transport.exists)

    def test_unknown_owner_after_interruption_is_quarantined(self):
        adapter, transport = self.adapter()
        transport.exists = True
        resource = adapter.resource("a"*32)
        transport.base = urlsplit(resource.id).path
        with self.assertRaises(ProviderError):
            adapter.cleanup(resource)
        self.assertFalse(any(method == "DELETE" for method, _ in transport.requests))

    def test_scope_and_dot_segments_are_rejected_before_transport(self):
        adapter, transport = self.adapter()
        for resource, key in ((Resource("https://foreign.example/", "a"*32), None),
                              (adapter.resource("a"*32), "..")):
            with self.assertRaises(ProviderError):
                adapter._request(resource, "GET", key=key)
        self.assertEqual(transport.requests, [])

    def test_operator_configuration_and_worker_codec_keep_profiles_and_secrets_separate(self):
        factory = provider_factory("webdav:demo", {"endpoint": "http://127.0.0.1:8080",
            "username": "test", "password_env": "DEMO_WEBDAV_PASSWORD"}, Path(self.tmp.name), "tenant")
        with patch.dict("os.environ", {"DEMO_WEBDAV_PASSWORD": "private-password",
            "PROOFBEAK_WEBDAV_DEMO_URL": "http://127.0.0.1:8080",
            "PROOFBEAK_WEBDAV_DEMO_USERNAME": "test",
            "PROOFBEAK_WEBDAV_DEMO_PASSWORD": "private-password"}):
            adapter = factory()
            rebuilt = decode_adapter(encode_adapter(adapter))
            self.assertEqual(adapter.profile, rebuilt.profile)
            self.assertEqual(adapters_for(["webdav:demo"])[0].profile, adapter.profile)
            self.assertNotIn("private-password", json.dumps(adapter.profile))
        with self.assertRaises(ValueError):
            encode_adapter(self.adapter()[0])

    def test_arbitrary_endpoints_names_and_embedded_credentials_are_rejected(self):
        for name, url in (("webdav:../bad", "http://127.0.0.1"),
                          ("webdav:test", "http://untrusted.example"),
                          ("webdav:test", "https://u:p@example.test"),
                          ("webdav:test", "https://example.test/path"),
                          ("webdav:test", "https://example.test?secret=x")):
            with self.subTest(name=name, url=url), self.assertRaises(ValueError):
                WebDAVAdapter(name, url, "test", "password")


if __name__ == "__main__":
    unittest.main()
