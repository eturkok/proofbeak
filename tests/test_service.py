import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.simulation import SimulationAdapter
from proofbeak.interfaces.mcp import BridgeClient
from proofbeak.service import ServiceError, Tenant, TrialService

ROOT = Path(__file__).resolve().parents[1]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tokens = {"a": "a" * 40, "b": "b" * 40}
        self.tenants = [Tenant(name, hashlib.sha256(token.encode()).hexdigest(), {
            "filesystem": lambda name=name: FilesystemAdapter(self.root / "objects" / name),
            "simulation:correct": SimulationAdapter,
        }) for name, token in self.tokens.items()]
        self.service = TrialService(self.root / "service", self.tenants, pending_limit=2)
        self.service.start(background=False)
        self.addCleanup(self.service.close)
        self.spec = json.loads((ROOT / "examples/storage.put_same_key.v1.json").read_text())

    def submit(self, tenant="a", key="key"):
        return self.service.submit(tenant, self.spec, ["filesystem"], key)["run_id"]

    def error(self, code, callback):
        with self.assertRaises(ServiceError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_token_resolves_tenant_and_invalid_token_is_rejected(self):
        self.assertEqual(self.service.authenticate(self.tokens["a"]), "a")
        self.error("unauthorized", lambda: self.service.authenticate("invalid"))
        self.error("unauthorized", lambda: self.service.authenticate("c" * 40))

    def test_discovery_examples_can_be_submitted_without_external_documentation(self):
        catalog = self.service.packs("a")
        example = next(p["example_test"] for p in catalog["packages"] if p["id"] == self.spec["id"])
        self.assertTrue(catalog["provider_capabilities"]["filesystem"]["configured"])
        run = self.service.submit("a", example, ["filesystem"], "discovered")
        self.service.process_next()
        self.assertEqual(self.service.get_run("a", run["run_id"])["candidates"][0]["verdict"], "pass")

    def test_queue_worker_comparison_and_evidence(self):
        run_id = self.submit()
        self.assertEqual(self.service.get_run("a", run_id)["state"], "queued")
        self.assertTrue(self.service.process_next())
        run = self.service.get_run("a", run_id)
        self.assertEqual(run["state"], "completed")
        self.assertEqual(run["candidates"][0]["verdict"], "pass")
        comparison = self.service.comparison("a", run_id)
        self.assertEqual(comparison["run_id"], run_id)
        self.assertNotIn("evidence", comparison["candidates"][0])
        evidence = self.service.evidence("a", run["candidates"][0]["evidence_ref"])
        self.assertIsNotNone(evidence["evidence"]["before"])
        self.assertEqual(evidence["cleanup_status"], "cleaned")
        self.assertFalse(self.service.process_next())

    def test_all_run_and_evidence_routes_are_tenant_bound(self):
        run_id = self.submit()
        self.service.process_next()
        for operation in [lambda: self.service.get_run("b", run_id),
                          lambda: self.service.comparison("b", run_id),
                          lambda: self.service.evidence("b", f"{run_id}:0"),
                          lambda: self.service.cleanup("b", run_id)]:
            self.error("not_found", operation)

    def test_same_key_is_scoped_to_tenant(self):
        first = self.submit("a")
        self.assertEqual(self.submit("a"), first)
        self.assertNotEqual(self.submit("b"), first)

    def test_same_key_changed_test_conflicts(self):
        self.submit()
        changed = {**self.spec, "fixture": {**self.spec["fixture"], "content": "changed"}}
        self.error("idempotency_conflict", lambda: self.service.submit(
            "a", changed, ["filesystem"], "key"))

    def test_repeated_completed_submission_does_not_execute_again(self):
        run_id = self.submit()
        self.service.process_next()
        self.assertEqual(self.submit(), run_id)
        self.assertFalse(self.service.process_next())

    def test_provider_allowlist_precedes_queueing(self):
        self.error("provider_not_allowed", lambda: self.service.submit(
            "a", self.spec, ["google-calendar"], "forbidden"))
        self.assertFalse(self.service.process_next())

    def test_mismatched_package_and_provider_rejected_before_queue(self):
        self.error("unsupported_trial_for_provider", lambda: self.service.submit(
            "a", self.spec, ["simulation:correct"], "wrong-package"))

    def test_invalid_trial_rejected_before_queue(self):
        self.error("invalid_trial", lambda: self.service.submit(
            "a", {"id": "arbitrary.code"}, ["filesystem"], "invalid"))

    def test_queue_limit_is_scoped_to_tenant(self):
        self.submit("a", "one")
        self.submit("a", "two")
        self.error("tenant_queue_full", lambda: self.submit("a", "three"))
        self.assertIsNotNone(self.submit("b", "one"))
        self.assertIsNotNone(self.submit("a", "one"))

    def test_pending_comparison_and_active_cleanup_are_explicit(self):
        run_id = self.submit()
        self.error("result_not_available", lambda: self.service.comparison("a", run_id))
        self.error("run_still_active", lambda: self.service.cleanup("a", run_id))

    def test_second_worker_cannot_share_state_directory(self):
        other = TrialService(self.root / "service", self.tenants)
        with self.assertRaisesRegex(RuntimeError, "another service worker"):
            other.start(background=False)

    def test_restart_does_not_replay_a_claimed_write(self):
        run_id = self.submit()
        with self.service._db() as db:
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (run_id,))
        self.service.close()
        replacement = TrialService(self.root / "service", self.tenants)
        replacement.start(background=False)
        self.addCleanup(replacement.close)
        run = replacement.get_run("a", run_id)
        self.assertEqual(run["state"], "interrupted")
        self.assertEqual(run["reason_code"], "recovery_required")
        self.assertFalse(replacement.process_next())
        self.assertFalse((self.root / "objects").exists())

    def test_queued_jobs_survive_restart(self):
        run_id = self.submit()
        self.service.close()
        replacement = TrialService(self.root / "service", self.tenants)
        replacement.start(background=False)
        self.addCleanup(replacement.close)
        self.assertTrue(replacement.process_next())
        self.assertEqual(replacement.get_run("a", run_id)["state"], "completed")

    def test_context_drift_stops_execution_before_provider_write(self):
        run_id = self.submit()
        self.tenants[0].providers["filesystem"] = lambda: FilesystemAdapter(self.root / "changed")
        self.service.process_next()
        run = self.service.get_run("a", run_id)
        self.assertEqual(run["state"], "interrupted")
        self.assertEqual(run["reason_code"], "execution_context_changed")
        self.assertFalse((self.root / "changed").exists())

    def test_client_credentials_are_not_persisted(self):
        self.service.authenticate(self.tokens["a"])
        self.submit()
        self.service.process_next()
        raw = self.service.database.read_bytes()
        self.assertNotIn(self.tokens["a"].encode(), raw)

    def test_evidence_id_traversal_and_foreign_indexes_are_rejected(self):
        for value in ["../../secret", "x:0", "a" * 32 + ":99"]:
            self.error("not_found", lambda value=value: self.service.evidence("a", value))
        run_id = self.submit()
        self.service.process_next()
        self.error("not_found", lambda: self.service.evidence("a", f"{run_id}:1"))


class BridgeBoundaryTests(unittest.TestCase):
    def test_remote_plaintext_and_url_credentials_are_rejected(self):
        for url in ["http://example.test", "https://user:secret@example.test",
                    "https://example.test/?token=secret", "https://example.test/path"]:
            with self.assertRaises(ValueError):
                BridgeClient(url, "a" * 40)

    def test_header_control_characters_are_rejected(self):
        for token in ["a" * 40 + "\nheader", "short", "a" * 40 + "\x00"]:
            with self.assertRaises(ValueError):
                BridgeClient("http://127.0.0.1:8765", token)

    def test_invalid_run_id_never_becomes_a_path(self):
        with self.assertRaises(ServiceError):
            BridgeClient.run_path("../../evidence")


if __name__ == "__main__":
    unittest.main()
