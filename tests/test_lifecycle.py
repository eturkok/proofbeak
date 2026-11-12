import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.simulation import SimulationAdapter
from proofbeak.service import ServiceError, Tenant, TrialService
from proofbeak.store import Store

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tokens = {"a": "a" * 40, "b": "b" * 40}
        self.tenants = [Tenant(name, hashlib.sha256(token.encode()).hexdigest(), {
            "filesystem": lambda name=name: FilesystemAdapter(self.root / name),
            "simulation:correct": SimulationAdapter,
        }) for name, token in self.tokens.items()]
        self.service = TrialService(self.root / "state", self.tenants)
        self.service.start(background=False)
        self.addCleanup(self.service.close)
        self.spec = json.loads((EXAMPLES / "storage.put_same_key.v1.json").read_text())

    def completed(self, owner="a", key="test"):
        run_id = self.service.submit(owner, self.spec, ["filesystem"], key)["run_id"]
        self.service.process_next()
        return run_id

    def assert_error(self, code, callback):
        with self.assertRaises(ServiceError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_exact_context_reuses_real_evidence_without_new_job(self):
        run_id = self.completed()
        found = self.service.lookup("a", self.spec, ["filesystem"])
        self.assertTrue(found["reusable"])
        self.assertEqual(found["run_id"], run_id)
        self.assertFalse(self.service.process_next())
        self.assertEqual(len(self.service.history("a")["runs"]), 1)

    def test_fresh_flag_does_not_return_or_create_cached_evidence(self):
        self.completed()
        result = self.service.lookup("a", self.spec, ["filesystem"], fresh=True)
        self.assertFalse(result["reusable"])
        self.assertEqual(result["reason_code"], "fresh_requested")
        self.assertFalse(self.service.process_next())

    def test_other_tenant_never_reuses_or_sees_the_result(self):
        run_id = self.completed()
        self.assertFalse(self.service.lookup("b", self.spec, ["filesystem"])["reusable"])
        self.assertEqual(self.service.history("b")["runs"], [])
        for operation in [lambda: self.service.revoke("b", run_id),
                          lambda: self.service.delete_run("b", run_id)]:
            self.assert_error("not_found", operation)

    def test_expired_evidence_stays_readable_but_is_not_reusable(self):
        run_id = self.completed()
        with self.service._db() as db:
            row = db.execute("SELECT result FROM jobs WHERE id=?", (run_id,)).fetchone()
            report = json.loads(row["result"])
            report["candidates"][0]["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            db.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(report), run_id))
        self.assertFalse(self.service.lookup("a", self.spec, ["filesystem"])["reusable"])
        evidence = self.service.evidence("a", f"{run_id}:0")
        self.assertIn("expired", evidence["validity"]["reasons"])

    def test_revocation_preserves_history_and_prevents_reuse(self):
        run_id = self.completed()
        self.service.revoke("a", run_id)
        self.assertFalse(self.service.lookup("a", self.spec, ["filesystem"])["reusable"])
        evidence = self.service.evidence("a", f"{run_id}:0")
        self.assertIn("revoked", evidence["validity"]["reasons"])
        self.assertEqual(evidence["verdict"], "pass")

    def test_simulation_is_not_cached_as_provider_proof(self):
        spec = json.loads((EXAMPLES / "calendar.move_one.v1.json").read_text())
        self.service.submit("a", spec, ["simulation:correct"], "sim")
        self.service.process_next()
        self.assertFalse(self.service.lookup("a", spec, ["simulation:correct"])["reusable"])

    def test_changed_fixture_and_changed_profile_do_not_reuse(self):
        run_id = self.completed()
        changed = {**self.spec, "fixture": {**self.spec["fixture"], "content": "new"}}
        self.assertFalse(self.service.lookup("a", changed, ["filesystem"])["reusable"])
        self.tenants[0].providers["filesystem"] = lambda: FilesystemAdapter(self.root / "different")
        self.assertFalse(self.service.lookup("a", self.spec, ["filesystem"])["reusable"])
        evidence = self.service.evidence("a", f"{run_id}:0")
        self.assertIn("context_changed", evidence["validity"]["reasons"])

    def test_tampered_observation_is_not_served_or_reused(self):
        run_id = self.completed()
        with self.service._db() as db:
            row = db.execute("SELECT result FROM jobs WHERE id=?", (run_id,)).fetchone()
            report = json.loads(row["result"])
            report["candidates"][0]["evidence"]["after"]["objects"] = {}
            db.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(report), run_id))
        self.assertFalse(self.service.lookup("a", self.spec, ["filesystem"])["reusable"])
        self.assert_error("evidence_integrity_failed", lambda: self.service.evidence("a", f"{run_id}:0"))

    def test_history_pages_are_bounded_and_tenant_scoped(self):
        first = self.completed(key="first")
        second = self.completed(key="second")
        page = self.service.history("a", limit=1)
        self.assertEqual(page["runs"][0]["run_id"], second)
        older = self.service.history("a", limit=1, before=page["next_cursor"])
        self.assertEqual(older["runs"][0]["run_id"], first)
        self.assertIsNone(older["next_cursor"])
        self.assert_error("not_found", lambda: self.service.history("b", before=first))

    def test_audit_records_metadata_without_tokens_or_fixture_payload(self):
        canary = "fixture-payload-that-must-not-be-in-audit"
        self.spec["fixture"]["content"] = canary
        run_id = self.completed()
        self.service.evidence("a", f"{run_id}:0")
        self.service.revoke("a", run_id)
        encoded = json.dumps(self.service.audit_events("a"))
        self.assertNotIn(canary, encoded)
        self.assertNotIn(self.tokens["a"], encoded)
        self.assertIn("evidence_read", encoded)
        self.assertIn("evidence_revoked", encoded)
        self.assertEqual(self.service.audit_events("b")["events"], [])

    def test_seven_day_raw_retention_redacts_both_databases(self):
        canary = "raw-canary-payload-to-remove-from-both-stores"
        self.spec["fixture"]["content"] = canary
        run_id = self.completed()
        ledger = self.service.root / "ledgers" / f"{run_id}.sqlite3"
        self.assertIn(canary.encode(), self.service.database.read_bytes())
        self.assertIn(canary.encode(), ledger.read_bytes())
        result = self.service.maintain(datetime.now(UTC) + timedelta(days=8))
        self.assertEqual(result["raw_purged"], 1)
        self.assertNotIn(canary.encode(), self.service.database.read_bytes())
        self.assertNotIn(canary.encode(), ledger.read_bytes())
        self.assert_error("raw_evidence_purged", lambda: self.service.evidence("a", f"{run_id}:0"))
        comparison = self.service.comparison("a", run_id)
        self.assertEqual(comparison["candidates"][0]["verdict"], "pass")
        self.assertTrue(comparison["test"]["redacted"])

    def test_summary_retention_deletes_data_but_reserves_the_old_key(self):
        run_id = self.completed()
        result = self.service.maintain(datetime.now(UTC) + timedelta(days=31))
        self.assertEqual(result["runs_deleted"], 1)
        self.assert_error("run_deleted", lambda: self.service.get_run("a", run_id))
        self.assert_error("run_deleted", lambda: self.service.submit("a", self.spec, ["filesystem"], "test"))
        self.assertFalse((self.service.root / "ledgers" / f"{run_id}.sqlite3").exists())

    def test_explicit_deletion_is_scoped_and_keeps_minimal_tombstone(self):
        run_id = self.completed()
        other = self.completed("b", "other")
        self.assertTrue(self.service.delete_run("a", run_id)["deleted"])
        self.assertEqual(self.service.get_run("b", other)["state"], "completed")
        self.assert_error("run_deleted", lambda: self.service.get_run("a", run_id))
        self.assert_error("not_found", lambda: self.service.get_run("b", run_id))

    def test_active_and_uncleaned_records_cannot_be_deleted(self):
        run_id = self.service.submit("a", self.spec, ["filesystem"], "active")["run_id"]
        self.assert_error("run_still_active", lambda: self.service.delete_run("a", run_id))
        self.service.process_next()
        ledger = self.service.root / "ledgers" / f"{run_id}.sqlite3"
        with closing(sqlite3.connect(ledger)) as db, db:
            db.execute("UPDATE resources SET status='quarantined'")
        self.assert_error("cleanup_required", lambda: self.service.delete_run("a", run_id))

    def test_pending_cleanup_defers_summary_deletion_but_not_raw_redaction(self):
        run_id = self.completed()
        ledger = self.service.root / "ledgers" / f"{run_id}.sqlite3"
        with closing(sqlite3.connect(ledger)) as db, db:
            db.execute("UPDATE resources SET status='quarantined'")
        result = self.service.maintain(datetime.now(UTC) + timedelta(days=31))
        self.assertEqual(result["raw_purged"], 1)
        self.assertEqual(result["deferred"], 1)
        self.assertEqual(self.service.get_run("a", run_id)["state"], "completed")

    def test_tenant_purge_removes_its_data_and_disables_its_credential(self):
        first = self.completed()
        second = self.completed("b", "survivor")
        result = self.service.purge_tenant("a")
        self.assertTrue(result["tenant_purged"])
        self.assert_error("unauthorized", lambda: self.service.authenticate(self.tokens["a"]))
        self.assertEqual(self.service.authenticate(self.tokens["b"]), "b")
        self.assertEqual(self.service.get_run("b", second)["state"], "completed")
        self.assert_error("not_found", lambda: self.service.get_run("a", first))
        with self.service._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit WHERE tenant='a'").fetchone()[0], 0)

    def test_busy_ledger_prevents_retention_from_racing_a_worker(self):
        run_id = self.completed()
        ledger = Store(self.service.root / "ledgers" / f"{run_id}.sqlite3")
        try:
            result = self.service.maintain(datetime.now(UTC) + timedelta(days=31))
            self.assertEqual(result["deferred"], 1)
            self.assertIsNone(self.service.get_run("a", run_id)["raw_purged_at"])
        finally:
            ledger.close()


if __name__ == "__main__":
    unittest.main()
