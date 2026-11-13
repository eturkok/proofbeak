"""Regression checks for coordinator races and operational failures."""

import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.service import ServiceError, Tenant, TrialService


class StabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tenant = Tenant("demo", hashlib.sha256(b"a" * 40).hexdigest(), {
            "filesystem": lambda: FilesystemAdapter(self.root / "objects")})
        self.service = TrialService(self.root / "state", [self.tenant])
        self.addCleanup(self.service.close)
        self.spec = json.loads((Path(__file__).resolve().parents[1] /
                               "examples/storage.put_same_key.v1.json").read_text())

    def test_deletion_during_retry_cannot_create_another_job(self):
        self.service.start(background=False)
        run_id = self.service.submit("demo", self.spec, ["filesystem"], "stable-key")["run_id"]
        self.service.process_next()
        original_context = self.service._context

        def delete_during_context(*args):
            self.service.delete_run("demo", run_id)
            return original_context(*args)

        with patch.object(self.service, "_context", side_effect=delete_during_context):
            with self.assertRaises(ServiceError) as raised:
                self.service.submit("demo", self.spec, ["filesystem"], "stable-key")
        self.assertEqual(raised.exception.code, "run_deleted")
        self.assertEqual(self.service.history("demo")["runs"], [])
        self.assertFalse(self.service.process_next())

    def test_finished_operation_locks_do_not_accumulate(self):
        for index in range(1000):
            with self.service._operation(str(index)):
                with self.assertRaises(ServiceError):
                    with self.service._operation(str(index)):
                        self.fail("overlapping cleanup was allowed")
        self.assertEqual(len(self.service.operation_locks), 0)

    def test_temporary_maintenance_error_does_not_stop_trials_or_leak_exception(self):
        with patch.object(self.service, "maintain", side_effect=sqlite3.OperationalError("private-error-canary")) as maintenance:
            self.service.start()
            run_id = self.service.submit("demo", self.spec, ["filesystem"], "maintenance-error")["run_id"]
            deadline = time.monotonic() + 5
            while self.service.get_run("demo", run_id)["state"] == "queued" and time.monotonic() < deadline:
                time.sleep(.01)
            while self.service.get_run("demo", run_id)["state"] == "running" and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(self.service.get_run("demo", run_id)["state"], "completed")
            self.assertTrue(self.service.worker.is_alive())
            self.assertEqual(maintenance.call_count, 1)
            self.assertNotIn("private-error-canary", json.dumps(self.service.audit_events("demo")))

    def test_dead_coordinator_rejects_new_jobs(self):
        with patch.object(self.service, "_loop", return_value=None):
            self.service.start()
            self.service.worker.join(timeout=1)
        with self.assertRaises(ServiceError) as raised:
            self.service.submit("demo", self.spec, ["filesystem"], "worker-dead")
        self.assertEqual(raised.exception.code, "service_unavailable")
        self.assertEqual(self.service.history("demo")["runs"], [])

    def test_dispatcher_storage_failure_exits_with_a_safe_readiness_reason(self):
        with patch.object(self.service, "process_next", side_effect=sqlite3.OperationalError("private-storage-canary")):
            self.service.start()
            self.service.worker.join(timeout=2)
        self.assertFalse(self.service.available)
        self.assertEqual(self.service.coordinator_failure, "coordinator_failed")
        with self.assertRaises(ServiceError):
            self.service.submit("demo", self.spec, ["filesystem"], "after-storage-failure")
