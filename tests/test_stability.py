"""Regression checks for coordinator races and operational failures."""

import hashlib
import gc
import json
import sqlite3
import tempfile
import time
import unittest
import warnings
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.service import ServiceError, Tenant, TrialService
from proofbeak.store import Store
from proofbeak.recovery import file_lock
from proofbeak.report import write_private
from proofbeak.cli import main as cli_main


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

    def test_failed_start_releases_owner_lock_for_recovery(self):
        with patch.object(self.service, "_db", side_effect=sqlite3.OperationalError("unavailable")):
            with self.assertRaises(sqlite3.OperationalError):
                self.service.start(background=False)
        self.assertIsNone(self.service.lock)
        self.service.start(background=False)
        self.assertTrue(self.service.available)

    def test_corrupt_ledger_does_not_leak_database_or_owner_lock(self):
        path = self.root / "broken.sqlite3"
        path.write_bytes(b"not-a-sqlite-database")
        gc.collect()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            with self.assertRaises(sqlite3.DatabaseError):
                Store(path)
            gc.collect()
        self.assertFalse([warning for warning in caught if issubclass(warning.category, ResourceWarning)])
        with file_lock(path.with_suffix(".sqlite3.lock")):
            pass

    def test_store_files_and_existing_store_are_owner_only(self):
        path = self.root / "private.sqlite3"
        store = Store(path)
        store.close()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.with_suffix(".sqlite3.lock").stat().st_mode & 0o777, 0o600)
        path.chmod(0o644)
        store = Store(path)
        store.close()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_report_failure_preserves_previous_export_and_removes_partial_file(self):
        path = self.root / "report.json"
        write_private(path, "original")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with patch("proofbeak.report.os.replace", side_effect=OSError("full disk")):
            with self.assertRaises(OSError):
                write_private(path, "replacement")
        self.assertEqual(path.read_text(), "original")
        self.assertEqual(list(self.root.glob(".proofbeak-report-*")), [])

    def test_cli_rejects_overlapping_export_paths_before_creating_state(self):
        spec = Path(__file__).resolve().parents[1] / "examples/storage.put_same_key.v1.json"
        database = self.root / "cli.sqlite3"
        output = self.root / "same-output"
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            code = cli_main(["run", str(spec), "--candidate", "filesystem", "--key", "overlap",
                             "--db", str(database), "--output", str(output), "--html", str(output)])
        self.assertEqual(code, 3)
        self.assertFalse(database.exists())

    def test_store_does_not_follow_database_symlink(self):
        original = self.root / "original"
        original.write_text("must remain unchanged")
        linked = self.root / "linked.sqlite3"
        linked.symlink_to(original)
        with self.assertRaises(OSError):
            Store(linked)
        self.assertEqual(original.read_text(), "must remain unchanged")

    def test_cli_cannot_replace_trial_or_database_with_report(self):
        spec = self.root / "trial.json"
        spec.write_text(json.dumps(self.spec))
        original = spec.read_bytes()
        database = self.root / "cli.sqlite3"
        for output in (spec, database, database.with_suffix(".sqlite3.lock"), Path(str(database) + "-wal")):
            with self.subTest(output=output), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                code = cli_main(["run", str(spec), "--candidate", "filesystem", "--key", "protected",
                                 "--db", str(database), "--output", str(output)])
            self.assertEqual(code, 3)
            self.assertFalse(database.exists())
            self.assertEqual(spec.read_bytes(), original)

    def test_cli_storage_error_is_an_operational_result_without_traceback(self):
        spec = self.root / "trial.json"
        spec.write_text(json.dumps(self.spec))
        database = self.root / "broken.sqlite3"
        database.write_bytes(b"not-a-database")
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as errors:
            code = cli_main(["run", str(spec), "--candidate", "filesystem", "--key", "broken",
                             "--db", str(database), "--output", str(self.root / "report.json")])
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", errors.getvalue())
        self.assertIn("same key", errors.getvalue())
