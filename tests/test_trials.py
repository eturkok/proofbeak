import copy
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, timedelta
from pathlib import Path
from unittest.mock import patch

from proofbeak.adapters.base import ProviderError, Resource
from proofbeak.adapters.radicale import RadicaleAdapter
from proofbeak.adapters.simulation import SimulationAdapter
from proofbeak.cli import main
from proofbeak.engine import recover_cleanup, run_trial
from proofbeak.models import Snapshot, Trial, fingerprint
from proofbeak.observer import assess
from proofbeak.report import render_html
from proofbeak.store import IdempotencyConflict, Store

SPEC_PATH = Path(__file__).resolve().parents[1] / "examples/calendar.move_one.v1.json"


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads(SPEC_PATH.read_text())
        self.trial = Trial.parse(self.spec)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "runs.sqlite3")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.store.close)

    def run_mode(self, mode="correct", key="test"):
        return run_trial(self.spec, [SimulationAdapter(mode)], self.store, key)["candidates"][0]

    def test_fixed_fixture(self):
        events = self.trial.fixture("series")
        self.assertEqual([e.start.day for e in events], [1, 8, 15, 22])
        self.assertEqual([e.start.hour for e in events], [10] * 4)

    def test_no_implicit_timezones(self):
        for start in ["2026-12-01T10:00:00", "2026-12-01T10:00:00+00:00"]:
            self.spec["fixture"]["start"] = start
            with self.assertRaises(ValueError):
                Trial.parse(self.spec)

    def test_closed_vocabulary(self):
        self.spec["action"]["op"] = "exec"
        with self.assertRaises(ValueError):
            Trial.parse(self.spec)

    def test_bounds_and_boolean_ints(self):
        for value in [True, 0, 1000, "4"]:
            self.spec["fixture"]["count"] = value
            with self.assertRaises(ValueError):
                Trial.parse(self.spec)

    def test_unknown_fields_rejected(self):
        self.spec["fixture"]["attendees"] = ["nobody@example.test"]
        with self.assertRaises(ValueError):
            Trial.parse(self.spec)

    def test_inconsistent_expectations_rejected(self):
        self.spec["expect"][0]["eq"] = 5
        with self.assertRaises(ValueError):
            Trial.parse(self.spec)

    def test_local_wall_time_across_dst(self):
        self.spec["fixture"].update(timezone="Europe/Berlin", start="2026-10-20T10:00:00+02:00")
        events = Trial.parse(self.spec).fixture("x")
        self.assertEqual([e.start.hour for e in events], [10] * 4)
        self.assertNotEqual(events[0].start.utcoffset(), events[1].start.utcoffset())

    def test_nonexistent_dst_time_rejected(self):
        self.spec["fixture"].update(timezone="Europe/Berlin", start="2026-03-22T02:30:00+01:00")
        with self.assertRaises(ValueError):
            Trial.parse(self.spec).fixture("x")

    def test_correct_move_has_independent_evidence(self):
        result = self.run_mode()
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["cleanup_status"], "cleaned")
        self.assertNotEqual(result["evidence"]["before"], result["evidence"]["after"])
        self.assertEqual(result["evidence_hash"], fingerprint(result["evidence"]))
        self.assertFalse(result["eligible_for_provider_comparison"])

    def test_move_all_is_detected(self):
        result = self.run_mode("move_all")
        self.assertEqual(result["verdict"], "fail")
        check = next(c for c in result["checks"] if c["field"] == "shifted_instances")
        self.assertEqual(check["observed"], [1, 2, 3, 4])

    def test_duplicate_is_detected(self):
        result = self.run_mode("duplicate")
        self.assertEqual(result["verdict"], "fail")
        self.assertFalse(result["checks"][-1]["passed"])

    def test_permission_failure_is_not_capability_failure(self):
        result = self.run_mode("permission_denied")
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertEqual(result["reason_code"], "permission_denied")
        self.assertEqual(result["cleanup_status"], "cleaned")

    def test_ambiguous_write_does_not_retry(self):
        adapter = SimulationAdapter("timeout")
        with patch.object(adapter, "move", wraps=adapter.move) as mutation:
            result = run_trial(self.spec, [adapter], self.store, "timeout")["candidates"][0]
        self.assertEqual(mutation.call_count, 1)
        self.assertEqual(adapter.reads, 2)
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertIsNotNone(result["evidence"]["after"])

    def test_successful_write_without_after_read_cannot_pass(self):
        adapter = SimulationAdapter()
        before = Snapshot(self.trial.fixture("unused"), "provider", "now")
        def read(resource, trial):
            if adapter.reads:
                raise ProviderError("observation_unavailable")
            adapter.reads += 1
            return replace(before, events=trial.fixture(resource.series_id))
        with patch.object(adapter, "read", side_effect=read):
            result = run_trial(self.spec, [adapter], self.store, "missing-after")["candidates"][0]
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertEqual(result["reason_code"], "observation_unavailable")

    def test_invalid_baseline_stops_mutation(self):
        adapter = SimulationAdapter()
        with patch.object(adapter, "read", return_value=Snapshot([], "provider", "now")), \
                patch.object(adapter, "move", side_effect=AssertionError("invalid baseline")):
            result = run_trial(self.spec, [adapter], self.store, "baseline")["candidates"][0]
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertEqual(result["reason_code"], "invalid_baseline")

    def test_idempotent_retry_does_not_provision(self):
        first = run_trial(self.spec, [SimulationAdapter()], self.store, "same")
        adapter = SimulationAdapter()
        with patch.object(adapter, "provision", side_effect=AssertionError("duplicate resource")):
            second = run_trial(self.spec, [adapter], self.store, "same")
        self.assertEqual(first, second)

    def test_same_key_different_request_conflicts(self):
        self.run_mode(key="same")
        with self.assertRaises(IdempotencyConflict):
            self.run_mode("duplicate", key="same")

    def test_interruption_never_replays(self):
        adapter = SimulationAdapter()
        context = [{"name": adapter.name, "version": adapter.version, "profile": adapter.profile,
                    "evidence_level": adapter.evidence_level}]
        request_hash = fingerprint({"test": self.spec, "candidates": context,
                                    "observer_version": "0.1.0"})
        run_id, _ = self.store.reserve("interrupted", request_hash)
        with patch.object(adapter, "provision", side_effect=AssertionError("must not replay")):
            report = run_trial(self.spec, [adapter], self.store, "interrupted")
        self.assertEqual(report["run_id"], run_id)
        self.assertEqual(report["reason_code"], "recovery_required")

    def test_cleanup_failure_is_quarantined_and_recoverable(self):
        adapter = SimulationAdapter()
        with patch.object(adapter, "cleanup", side_effect=ProviderError("permission_denied")):
            result = run_trial(self.spec, [adapter], self.store, "cleanup")["candidates"][0]
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["cleanup_status"], "quarantined")
        self.assertEqual(len(self.store.pending()), 1)
        self.assertEqual(recover_cleanup(self.store, [adapter])[0]["status"], "cleaned")
        self.assertEqual(self.store.pending(), [])

    def test_cleanup_requires_matching_profile(self):
        adapter = SimulationAdapter()
        with patch.object(adapter, "cleanup", side_effect=ProviderError("network_error")):
            run_trial(self.spec, [adapter], self.store, "cleanup")
        other = SimulationAdapter()
        other.profile = {"mode": "different"}
        self.assertEqual(recover_cleanup(self.store, [other])[0]["status"], "profile_unavailable")

    def test_corrupt_resource_cannot_escape_scope(self):
        adapter = SimulationAdapter()
        run_id, _ = self.store.reserve("cleanup", "hash")
        self.store.track(run_id, adapter.name, fingerprint(adapter.profile),
                         {"id": "unowned-resource", "series_id": "other"})
        self.assertEqual(recover_cleanup(self.store, [adapter])[0]["status"], "resource_scope_violation")

    def test_active_worker_prevents_concurrent_cleanup(self):
        with self.assertRaisesRegex(ValueError, "another local worker"):
            Store(Path(self.tmp.name) / "runs.sqlite3")

    def test_resource_conflict_never_deletes_unowned_collection(self):
        adapter = SimulationAdapter()
        with patch.object(adapter, "provision", side_effect=ProviderError("resource_conflict")), \
                patch.object(adapter, "cleanup", side_effect=AssertionError("unowned resource")):
            result = run_trial(self.spec, [adapter], self.store, "conflict")["candidates"][0]
            self.assertEqual(result["cleanup_status"], "ownership_unverified")
            self.assertEqual(recover_cleanup(self.store, [adapter])[0]["status"], "ownership_unverified")

    def test_ambiguous_dst_time_rejected(self):
        self.spec["fixture"].update(timezone="Europe/Berlin", start="2026-10-25T02:30:00+02:00")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            Trial.parse(self.spec).fixture("x")

    def test_missing_instance_cannot_pass(self):
        before = Snapshot(self.trial.fixture("x"), "read", "now")
        after = Snapshot(before.events[:-1], "read", "now")
        self.assertEqual(assess(self.trial, before, after)["verdict"], "fail")

    def test_wrong_baseline_is_inconclusive(self):
        before = Snapshot(self.trial.fixture("x")[:-1], "read", "now")
        self.assertEqual(assess(self.trial, before, before)["verdict"], "inconclusive")

    def test_utc_observations_match_local_fixture(self):
        before = Snapshot([replace(e, start=e.start.astimezone(UTC), end=e.end.astimezone(UTC),
                                   original_start=e.original_start.astimezone(UTC))
                           for e in self.trial.fixture("x")], "read", "now")
        events = list(before.events)
        event = events[1]
        events[1] = replace(event, start=event.start + timedelta(hours=1),
                            end=event.end + timedelta(hours=1))
        self.assertEqual(assess(self.trial, before, Snapshot(events, "read", "now"))["verdict"], "pass")

    def test_duration_change_cannot_pass(self):
        before = Snapshot(self.trial.fixture("x"), "read", "now")
        after_events = copy.copy(before.events)
        event = after_events[1]
        after_events[1] = replace(event, start=event.start + timedelta(hours=1),
                                  end=event.end + timedelta(minutes=90))
        self.assertEqual(assess(self.trial, before, Snapshot(after_events, "read", "now"))["verdict"], "fail")

    def test_too_many_candidates_rejected_before_any_side_effect(self):
        with self.assertRaises(ValueError):
            run_trial(self.spec, [SimulationAdapter()] * 5, self.store, "many")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)

    def test_html_escapes_untrusted_provider_text(self):
        result = self.run_mode()
        result["provider"] = '<script>alert("x")</script>'
        html = render_html({"run_id": "x", "candidates": [result]})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_cli_report_and_exit_codes(self):
        output = Path(self.tmp.name) / "report.json"
        html = Path(self.tmp.name) / "report.html"
        db = str(Path(self.tmp.name) / "cli.sqlite3")
        code = main(["run", str(SPEC_PATH), "--candidate", "simulation:correct",
                     "--candidate", "simulation:move_all", "--key", "cli",
                     "--db", db, "--output", str(output), "--html", str(html)])
        self.assertEqual(code, 1)
        self.assertEqual(len(json.loads(output.read_text())["candidates"]), 2)
        self.assertIn("Proofbeak", html.read_text())


class AdapterBoundaryTests(unittest.TestCase):
    def test_radicale_rejects_credentials_in_url_and_remote_plaintext(self):
        for endpoint in ["https://user:secret@example.test", "http://example.test",
                         "https://example.test/?token=secret", "https://example.test/path"]:
            with self.assertRaises(ValueError):
                RadicaleAdapter(endpoint, "proofbeak", "password")

    def test_radicale_rejects_scope_escape(self):
        adapter = RadicaleAdapter("http://127.0.0.1:5232", "proofbeak", "password")
        with self.assertRaises(ProviderError):
            adapter.cleanup(Resource("http://127.0.0.1:5232/someone-else/", "a" * 32))

    def test_secrets_do_not_enter_profile(self):
        adapter = RadicaleAdapter("http://127.0.0.1:5232", "proofbeak", "sensitive-password")
        self.assertNotIn("sensitive-password", json.dumps(adapter.profile))


if __name__ == "__main__":
    unittest.main()
