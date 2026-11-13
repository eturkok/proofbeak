"""Provider protocol and fault tests. The injected transport is explicitly simulated evidence."""

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from proofbeak.adapters.base import ProviderError, Resource
from proofbeak.adapters.google import ORIGIN, GoogleCalendarAdapter
from proofbeak.adapters.http import Response
from proofbeak.engine import recover_cleanup, run_trial
from proofbeak.packs.calendar import CalendarPack
from proofbeak.store import Store

ACCOUNT = "dedicated-test@example.test"
SPEC = Path(__file__).resolve().parents[1] / "examples/calendar.move_one.v1.json"


class ScriptedGoogle:
    """In-memory HTTP contract fixture. This is not a real Google service."""

    def __init__(self, mode="correct"):
        self.mode = mode
        self.calls = 0
        self.requests = []
        self.calendar = None
        self.events = []
        self.deleted = False

    def request(self, method, url, *, body=None, headers=None, allowed=(200,)):
        self.calls += 1
        if self.calls > 24:
            raise ProviderError("request_budget_exceeded")
        parsed = urlsplit(url)
        path = unquote(parsed.path.removeprefix("/calendar/v3"))
        query = parse_qs(parsed.query)
        payload = json.loads(body) if body else None
        self.requests.append((method, path, payload, headers, query))
        if not url.startswith(ORIGIN + "/"):
            raise AssertionError("unexpected origin")
        status, result = 200, {}
        if path == "/users/me/calendarList/primary":
            result = {"id": "different@example.test" if self.mode == "wrong_account" else ACCOUNT,
                      "primary": True, "accessRole": "owner"}
        elif path == "/users/me/calendarList":
            entries = [] if not self.calendar or self.deleted else [self.entry()]
            result = {"items": entries}
            if self.mode == "pagination_loop":
                result["nextPageToken"] = "same-page"
            if self.mode == "ambiguous_discovery" and entries:
                result["items"].append({**entries[0], "id": "second-secondary"})
        elif path == "/calendars" and method == "POST":
            self.calendar = {**payload, "id": "generated@group.calendar.google.com"}
            result = self.calendar
            if self.mode == "create_timeout":
                raise ProviderError("write_outcome_unknown")
            if self.mode == "primary_response":
                result = {**result, "id": "primary"}
        elif path.startswith("/users/me/calendarList/"):
            status, result = (404, {}) if self.deleted else (200, self.entry())
            if self.mode == "foreign_metadata":
                result = {**result, "description": "someone else's calendar"}
        elif path.endswith("/events") and method == "POST":
            start = datetime.fromisoformat(payload["start"]["dateTime"])
            end = datetime.fromisoformat(payload["end"]["dateTime"])
            self.events = [{
                "id": f"instance-{i}", "recurringEventId": payload["id"],
                "originalStartTime": {"dateTime": (start + timedelta(weeks=i)).isoformat()},
                "start": {"dateTime": (start + timedelta(weeks=i)).isoformat()},
                "end": {"dateTime": (end + timedelta(weeks=i)).isoformat()},
                "status": "confirmed", "etag": '"version-1"',
            } for i in range(4)]
            result = {"id": payload["id"]}
        elif path.endswith("/events") and method == "GET":
            result = {"items": copy.deepcopy(self.events)}
            if self.mode == "invalid_observation" and result["items"] and any(
                r[0] == "PUT" for r in self.requests
            ):
                result["items"][0]["end"] = {"date": "2026-12-01"}
        elif "/events/instance-" in path:
            event = next(e for e in self.events if e["id"] == path.rsplit("/", 1)[1])
            if method == "PUT":
                if headers.get("If-Match") != event["etag"]:
                    raise AssertionError("missing concurrency control")
                if self.mode == "write_timeout":
                    raise ProviderError("write_outcome_unknown")
                if self.mode == "etag_conflict":
                    raise ProviderError("concurrent_modification")
                event.update(payload)
                if self.mode == "move_all":
                    for other in self.events:
                        if other is not event:
                            for field in ("start", "end"):
                                value = datetime.fromisoformat(other[field]["dateTime"])
                                other[field] = {"dateTime": (value + timedelta(hours=1)).isoformat()}
                if self.mode == "duplicate":
                    self.events.append(copy.deepcopy(event))
            result = copy.deepcopy(event)
            if self.mode == "missing_etag":
                result.pop("etag", None)
            if self.mode == "has_attendees":
                result["attendees"] = [{"email": "nobody@example.test"}]
        elif path.startswith("/calendars/") and method == "DELETE":
            if self.mode == "cleanup_denied":
                raise ProviderError("permission_denied")
            self.deleted = self.mode != "cleanup_unconfirmed"
            status = 204
        elif path.startswith("/calendars/") and method == "GET":
            status, result = (404, {}) if self.deleted else (200, {"id": self.calendar["id"]})
        else:
            raise AssertionError(f"unexpected request {method} {path}")
        if status not in allowed:
            raise ProviderError("provider_http_error")
        return Response(status, json.dumps(result).encode() if status != 204 else b"", {})

    def entry(self):
        return {**self.calendar, "accessRole": "owner", "primary": False}


class GoogleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "runs.sqlite3")
        self.addCleanup(self.store.close)
        self.spec = json.loads(SPEC.read_text())

    def adapter(self, mode="correct"):
        transport = ScriptedGoogle(mode)
        return GoogleCalendarAdapter("short-lived-test-token", ACCOUNT, transport), transport

    def run_mode(self, mode):
        adapter, transport = self.adapter(mode)
        report = run_trial(self.spec, [adapter], self.store, mode)
        return report["candidates"][0], transport

    def test_protocol_round_trip_and_cleanup(self):
        result, transport = self.run_mode("correct")
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["cleanup_status"], "cleaned")
        self.assertEqual(result["evidence_level"], "simulation")
        self.assertFalse(result["eligible_for_provider_comparison"])
        self.assertTrue(transport.deleted)
        self.assertEqual(len(result["evidence"]["after"]["events"]), 4)
        mutations = [r for r in transport.requests if r[0] in {"POST", "PUT", "DELETE"}]
        self.assertTrue(all("/primary" not in r[1] for r in mutations))
        event_writes = [r for r in mutations if "/events" in r[1]]
        self.assertTrue(all(r[4]["sendUpdates"] == ["none"] for r in event_writes))
        self.assertTrue(all("attendees" not in r[2] for r in event_writes))

    def test_preflight_reads_only_identity_metadata(self):
        adapter, transport = self.adapter()
        result = adapter.preflight()
        self.assertTrue(result["account_verified"])
        self.assertFalse(result["write_access_verified"])
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(transport.requests[0][0], "GET")

    def test_wrong_account_stops_before_mutation(self):
        result, transport = self.run_mode("wrong_account")
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertEqual(result["reason_code"], "test_account_mismatch")
        self.assertTrue(all(r[0] == "GET" for r in transport.requests))

    def test_permission_error_is_inconclusive(self):
        adapter, _ = self.adapter()
        def denied(*args, **kwargs):
            raise ProviderError("permission_denied")
        adapter.transport.request = denied
        result = run_trial(self.spec, [adapter], self.store, "denied")["candidates"][0]
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertEqual(result["reason_code"], "permission_denied")

    def test_provider_behavior_faults_are_detected(self):
        for mode in ("move_all", "duplicate"):
            with self.subTest(mode=mode):
                result, _ = self.run_mode(mode)
                self.assertEqual(result["verdict"], "fail")

    def test_create_timeout_recovers_by_owned_marker_without_retrying_create(self):
        result, transport = self.run_mode("create_timeout")
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertEqual(result["reason_code"], "write_outcome_unknown")
        self.assertEqual(result["cleanup_status"], "cleaned")
        self.assertEqual(sum(r[0] == "POST" and r[1] == "/calendars" for r in transport.requests), 1)

    def test_invalid_after_read_cannot_pass(self):
        result, _ = self.run_mode("invalid_observation")
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertEqual(result["reason_code"], "invalid_observation")

    def test_concurrency_and_unknown_write_are_inconclusive(self):
        for mode, reason in [("write_timeout", "write_outcome_unknown"),
                             ("etag_conflict", "concurrent_modification"),
                             ("missing_etag", "missing_concurrency_token")]:
            with self.subTest(mode=mode):
                result, transport = self.run_mode(mode)
                self.assertEqual(result["verdict"], "inconclusive")
                self.assertEqual(result["reason_code"], reason)
                self.assertLessEqual(sum(r[0] == "PUT" for r in transport.requests), 1)

    def test_invitations_are_rejected(self):
        result, transport = self.run_mode("has_attendees")
        self.assertEqual(result["reason_code"], "resource_scope_violation")
        self.assertFalse(any(r[0] == "PUT" for r in transport.requests))

    def test_primary_calendar_response_is_never_mutated(self):
        result, transport = self.run_mode("primary_response")
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertFalse(any("/primary" in r[1] and r[0] != "GET" for r in transport.requests))

    def test_foreign_metadata_is_not_deleted(self):
        result, transport = self.run_mode("foreign_metadata")
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertEqual(result["cleanup_status"], "quarantined")
        self.assertFalse(any(r[0] == "DELETE" for r in transport.requests))

    def test_cleanup_permission_and_verification_failures_remain_separate(self):
        for mode in ("cleanup_denied", "cleanup_unconfirmed"):
            result, _ = self.run_mode(mode)
            self.assertEqual(result["verdict"], "pass")
            self.assertEqual(result["cleanup_status"], "quarantined")

    def test_fresh_worker_can_discover_and_clean_recorded_resource(self):
        adapter, transport = self.adapter("cleanup_denied")
        run_trial(self.spec, [adapter], self.store, "recover")
        transport.mode = "correct"
        replacement = GoogleCalendarAdapter("rotated-test-token", ACCOUNT, transport)
        result = recover_cleanup(self.store, [replacement])
        self.assertEqual(result[0]["status"], "cleaned")
        self.assertTrue(transport.deleted)

    def test_ambiguous_marker_is_not_deleted(self):
        adapter, transport = self.adapter("cleanup_denied")
        run_trial(self.spec, [adapter], self.store, "ambiguous")
        transport.mode = "ambiguous_discovery"
        replacement = GoogleCalendarAdapter("rotated-test-token", ACCOUNT, transport)
        self.assertEqual(recover_cleanup(self.store, [replacement])[0]["status"], "quarantined")
        self.assertFalse(transport.deleted)

    def test_pagination_loop_cannot_hide_missing_observation(self):
        result, _ = self.run_mode("pagination_loop")
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertEqual(result["reason_code"], "invalid_pagination")

    def test_scope_escape_is_rejected_before_network(self):
        adapter, transport = self.adapter()
        with self.assertRaises(ProviderError):
            adapter.cleanup(Resource("primary", "a" * 32))
        self.assertEqual(transport.requests, [])

    def test_tokens_and_account_email_are_not_recorded_in_evidence(self):
        result, _ = self.run_mode("correct")
        encoded = json.dumps(result)
        self.assertNotIn("short-lived-test-token", encoded)
        self.assertNotIn(ACCOUNT, encoded)

    def test_invalid_credentials_fail_before_any_request(self):
        for token in ("", "Bearer secret", "secret\nheader", "secret\x00header"):
            with self.assertRaises(ValueError):
                GoogleCalendarAdapter(token, ACCOUNT)

    def test_same_calendar_package_and_fixture_used(self):
        adapter, _ = self.adapter()
        trial = CalendarPack().parse(self.spec)
        self.assertEqual(trial.count, 4)
        self.assertIn(CalendarPack.id, adapter.supported_trials)


if __name__ == "__main__":
    unittest.main()
