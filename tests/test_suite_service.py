import copy
import hashlib
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.scenario import ScenarioAdapter
from proofbeak.recovery import backup, restore
from proofbeak.service import ServiceError, Tenant, TrialService
from proofbeak.suites import example_suite


class SuiteServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tenants = [Tenant(name, hashlib.sha256((name * 40).encode()).hexdigest(), {
            "scenario:filesystem": lambda name=name: ScenarioAdapter(FilesystemAdapter(self.root / "objects" / name))})
            for name in ("a", "b")]
        self.service = TrialService(self.root / "state", self.tenants, pending_limit=1)
        self.service.start(background=False)
        self.addCleanup(self.service.close)
        self.definition = example_suite()

    def submit(self, key="suite", tenant="a"):
        return self.service.submit_suite(tenant, self.definition, key)["suite_id"]

    def finish(self):
        while self.service.process_next():
            pass

    def test_bounded_suite_uses_one_queue_slot_and_retrieves_without_new_jobs(self):
        suite_id = self.submit()
        with self.assertRaises(ServiceError) as error:
            self.submit("second")
        self.assertEqual(error.exception.code, "tenant_queue_full")
        self.finish()
        result = self.service.get_suite("a", suite_id)
        self.assertEqual(result["gate"], "pass")
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["counts"]["pass"], 3)
        self.assertEqual(self.submit(), suite_id)
        self.assertFalse(self.service.process_next())
        self.assertEqual(len(self.service.history("a")["runs"]), 3)

    def test_validate_is_mutation_free_and_invalid_last_member_reserves_nothing(self):
        self.assertTrue(self.service.validate_suite("a", self.definition)["valid"])
        self.assertEqual(self.service.list_suites("a")["suites"], [])
        self.assertFalse((self.root / "objects").exists())
        bad = copy.deepcopy(self.definition["cases"][0])
        bad["id"] = "foreign"
        bad["candidates"] = ["scenario:foreign"]
        self.definition["cases"].append(bad)
        with self.assertRaises(ServiceError):
            self.submit()
        self.assertEqual(self.service.list_suites("a")["suites"], [])

    def test_tenant_bound_read_cancel_and_idempotency(self):
        suite_id = self.submit()
        for action in (lambda: self.service.get_suite("b", suite_id), lambda: self.service.cancel_suite("b", suite_id)):
            with self.assertRaises(ServiceError) as error:
                action()
            self.assertEqual(error.exception.status, 404)
        self.assertNotEqual(self.submit(tenant="b"), suite_id)
        self.definition["name"] = "Changed"
        with self.assertRaises(ServiceError) as error:
            self.submit()
        self.assertEqual(error.exception.code, "idempotency_conflict")

    def test_cancel_prevents_all_remaining_provider_writes(self):
        suite_id = self.submit()
        self.service.process_next()
        result = self.service.cancel_suite("a", suite_id)
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(result["gate"], "inconclusive")
        self.assertFalse(self.service.process_next())
        self.assertEqual(len(self.service.history("a")["runs"]), 1)
        self.assertIsNotNone(self.submit("new"))

    def test_restart_continues_only_planned_members_and_never_repeats_completed_writes(self):
        suite_id = self.submit()
        self.service.process_next()
        first = self.service.get_suite("a", suite_id)["entries"][0]["report"]
        self.service.close()
        self.service = TrialService(self.root / "state", self.tenants, pending_limit=1)
        self.service.start(background=False)
        self.addCleanup(self.service.close)
        self.finish()
        result = self.service.get_suite("a", suite_id)
        self.assertEqual(result["gate"], "pass")
        previous = copy.deepcopy(first)
        latest = copy.deepcopy(result["entries"][0]["report"])
        for report in (previous, latest):
            for candidate in report["candidates"]:
                candidate["validity"].pop("checked_at", None)
        self.assertEqual(previous, latest)
        self.assertEqual(len(self.service.history("a")["runs"]), 3)

    def test_restore_interrupts_pending_suite_instead_of_executing_planned_writes(self):
        suite_id = self.submit()
        self.service.process_next()
        self.service.close()
        backup(self.root / "state", self.root / "snapshot")
        restore(self.root / "snapshot", self.root / "restored")
        recovered = TrialService(self.root / "restored", self.tenants)
        recovered.start(background=False)
        self.addCleanup(recovered.close)
        self.assertFalse(recovered.process_next())
        result = recovered.get_suite("a", suite_id)
        self.assertEqual(result["state"], "interrupted")
        self.assertEqual(result["reason_code"], "state_restored")
        self.assertEqual(len(recovered.history("a")["runs"]), 1)

    def test_retention_redacts_duplicate_inputs_and_keeps_suite_key_tombstone(self):
        suite_id = self.submit()
        self.finish()
        now = datetime.now(UTC)
        self.service.maintain(now + timedelta(days=8))
        result = self.service.get_suite("a", suite_id)
        self.assertTrue(result["definition"]["redacted"])
        self.assertTrue(all(e["parameters"] == {} for e in result["entries"]))
        self.service.maintain(now + timedelta(days=31))
        with self.assertRaises(ServiceError) as error:
            self.submit()
        self.assertEqual(error.exception.code, "suite_deleted")

    def test_tenant_purge_removes_suite_definitions_and_preserves_other_tenant(self):
        a, b = self.submit(), self.submit(tenant="b")
        self.finish()
        self.service.purge_tenant("a")
        with self.assertRaises(ServiceError):
            self.service.get_suite("a", a)
        self.assertEqual(self.service.get_suite("b", b)["gate"], "pass")

    def test_running_child_at_crash_is_interrupted_and_blocks_remaining_members(self):
        suite_id = self.submit()
        with self.service._db() as db:
            self.service._schedule_suites(db)
            db.execute("UPDATE jobs SET state='running'")
        self.service.close()
        self.service = TrialService(self.root / "state", self.tenants)
        self.service.start(background=False)
        self.addCleanup(self.service.close)
        self.assertFalse(self.service.process_next())
        result = self.service.get_suite("a", suite_id)
        self.assertEqual(result["state"], "interrupted")
        self.assertEqual(len(self.service.history("a")["runs"]), 1)
