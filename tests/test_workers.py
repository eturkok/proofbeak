import hashlib
import json
import os
import sqlite3
import tempfile
import time
import unittest
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.simulation import SimulationAdapter
from proofbeak.executor import SubprocessExecutor, WorkerLimits
from proofbeak.service import ServiceError, Tenant, TrialService
from proofbeak.store import Store
from proofbeak.worker_adapters import decode_adapter, encode_adapter

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.calendar = json.loads((EXAMPLES / "calendar.move_one.v1.json").read_text())
        self.storage = json.loads((EXAMPLES / "storage.put_same_key.v1.json").read_text())
        self.executor = SubprocessExecutor(WorkerLimits(wall_seconds=2))
        self.ledger = self.root / "trial.sqlite3"

    def test_actual_child_executes_and_reports_resource_limits(self):
        result = self.executor.run(self.storage, [FilesystemAdapter(self.root / "objects")], self.ledger, "child")
        self.assertIsNone(result.reason)
        self.assertNotEqual(result.pid, os.getpid())
        report = result.payload["report"]
        self.assertEqual(report["candidates"][0]["verdict"], "pass")
        self.assertTrue(report["runner"]["limits"]["process_separation"])
        if os.uname().sysname == "Linux":
            self.assertTrue(report["runner"]["limits"]["memory_limit_enforced"])
        self.assertEqual(list((self.root / "objects").iterdir()), [])

    def test_timeout_kills_worker_and_keeps_cleanup_intent(self):
        executor = SubprocessExecutor(WorkerLimits(wall_seconds=0.4))
        result = executor.run(self.calendar, [SimulationAdapter("hang")], self.ledger, "timeout")
        self.assertEqual(result.reason, "trial_timed_out")
        self.assertLess(result.elapsed, 2)
        with self.assertRaises(ProcessLookupError):
            os.kill(result.pid, 0)
        store = Store(self.ledger)
        try:
            self.assertEqual(len(store.pending()), 1)
        finally:
            store.close()
        recovered = executor.cleanup([SimulationAdapter("hang")], self.ledger)
        self.assertIsNone(recovered.reason)
        self.assertEqual(recovered.payload["outcomes"][0]["status"], "cleaned")

    def test_abrupt_crash_does_not_erase_the_resource_ledger(self):
        result = self.executor.run(self.calendar, [SimulationAdapter("crash")], self.ledger, "crash")
        self.assertEqual(result.reason, "worker_crashed")
        store = Store(self.ledger)
        try:
            self.assertEqual(len(store.pending()), 1)
        finally:
            store.close()

    def test_parent_credentials_are_not_inherited_or_persisted(self):
        sentinel = "private-api-token-never-for-worker"
        with patch.dict(os.environ, {"PROOFBEAK_API_TOKEN": sentinel, "UNRELATED_CREDENTIAL": sentinel}):
            result = self.executor.run(self.storage, [FilesystemAdapter(self.root / "objects")],
                                       self.ledger, "secrets")
        self.assertIsNone(result.reason)
        self.assertNotIn(sentinel, json.dumps(result.payload))
        self.assertNotIn(sentinel.encode(), self.ledger.read_bytes())

    def test_codec_preserves_provider_context_without_arbitrary_imports(self):
        adapter = FilesystemAdapter(self.root / "objects")
        rebuilt = decode_adapter(encode_adapter(adapter))
        self.assertEqual(adapter.profile, rebuilt.profile)
        with self.assertRaises(ValueError):
            decode_adapter({"type": "os.system", "options": {"command": "anything"}})

    def test_active_ledger_cannot_be_cleaned_by_another_worker(self):
        store = Store(self.ledger)
        try:
            result = self.executor.cleanup([SimulationAdapter()], self.ledger)
            self.assertEqual(result.reason, "run_still_active")
        finally:
            store.close()

    def test_cancellation_terminates_actual_worker(self):
        results = []
        thread = Thread(target=lambda: results.append(self.executor.run(
            self.calendar, [SimulationAdapter("hang")], self.ledger, "cancel")))
        thread.start()
        deadline = time.monotonic() + 2
        while not self.executor.active and time.monotonic() < deadline:
            time.sleep(0.01)
        self.executor.cancel("cancel")
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0].reason, "worker_cancelled")

    def test_runtime_limits_reject_unbounded_or_invalid_values(self):
        for settings in [{"wall_seconds": 0}, {"cpu_seconds": True}, {"memory_mib": 1}]:
            with self.assertRaises(ValueError):
                WorkerLimits(**settings)

    def test_worker_stops_when_its_api_parent_disappears(self):
        script = '''
import json, sqlite3, sys, time
from contextlib import closing
from pathlib import Path
from threading import Thread
from proofbeak.adapters.simulation import SimulationAdapter
from proofbeak.executor import SubprocessExecutor, WorkerLimits
root = Path(sys.argv[1])
spec = json.loads(Path(sys.argv[2]).read_text())
executor = SubprocessExecutor(WorkerLimits(wall_seconds=10))
Thread(target=lambda: executor.run(spec, [SimulationAdapter("hang")], root / "orphan.sqlite3", "orphan")).start()
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    try:
        with closing(sqlite3.connect(root / "orphan.sqlite3")) as db, db:
            ready = db.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 1
        if ready and executor.active:
            (root / "child.pid").write_text(str(next(iter(executor.active))))
            break
    except sqlite3.OperationalError:
        pass
    time.sleep(.02)
else:
    raise SystemExit(2)
time.sleep(30)
'''
        parent = subprocess.Popen([sys.executable, "-c", script, str(self.root),
                                   str(EXAMPLES / "calendar.move_one.v1.json")],
                                  env={**os.environ, "PYTHONPATH": str(EXAMPLES.parent / "src")},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        child_pid = None
        try:
            deadline = time.monotonic() + 5
            marker = self.root / "child.pid"
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(marker.exists())
            child_pid = int(marker.read_text())
            parent.terminate()
            parent.wait(timeout=3)
            deadline = time.monotonic() + 3
            recovered = None
            while time.monotonic() < deadline:
                try:
                    recovered = Store(self.root / "orphan.sqlite3")
                    break
                except ValueError:
                    time.sleep(.02)
            self.assertIsNotNone(recovered, "orphan worker kept its resource ledger locked")
            try:
                self.assertEqual(len(recovered.pending()), 1)
            finally:
                recovered.close()
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=3)
            if child_pid:
                try:
                    os.killpg(child_pid, 9)
                except ProcessLookupError:
                    pass

    def test_process_environment_is_deliberately_minimal(self):
        import subprocess

        original = subprocess.Popen
        captured = []
        def spawn(*args, **kwargs):
            captured.append(kwargs)
            return original(*args, **kwargs)
        with patch("proofbeak.executor.subprocess.Popen", side_effect=spawn):
            result = self.executor.run(self.storage, [FilesystemAdapter(self.root / "objects")], self.ledger, "env")
        self.assertIsNone(result.reason)
        self.assertNotIn("PROOFBEAK_API_TOKEN", captured[0]["env"])
        self.assertNotIn("UNRELATED_CREDENTIAL", captured[0]["env"])
        self.assertTrue(captured[0]["start_new_session"])
        self.assertNotIn("preexec_fn", captured[0])


class WorkerServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tenant = Tenant("owner", hashlib.sha256(b"a" * 40).hexdigest(), {
            "simulation:hang": lambda: SimulationAdapter("hang"),
            "simulation:crash": lambda: SimulationAdapter("crash"),
            "filesystem": lambda: FilesystemAdapter(self.root / "objects")})
        self.service = TrialService(self.root / "state", [self.tenant],
            executor=SubprocessExecutor(WorkerLimits(wall_seconds=0.5)))
        self.service.start(background=False)
        self.addCleanup(self.service.close)
        self.calendar = json.loads((EXAMPLES / "calendar.move_one.v1.json").read_text())
        self.storage = json.loads((EXAMPLES / "storage.put_same_key.v1.json").read_text())

    def test_timed_out_run_does_not_block_next_trial(self):
        first = self.service.submit("owner", self.calendar, ["simulation:hang"], "first")["run_id"]
        second = self.service.submit("owner", self.storage, ["filesystem"], "second")["run_id"]
        self.service.process_next()
        timed_out = self.service.get_run("owner", first)
        self.assertEqual(timed_out["state"], "interrupted")
        self.assertEqual(timed_out["reason_code"], "trial_timed_out")
        self.assertEqual(timed_out["candidates"], [])
        self.service.process_next()
        self.assertEqual(self.service.get_run("owner", second)["state"], "completed")
        self.assertEqual(self.service.cleanup("owner", first)["cleanup_recovery"][0]["status"], "cleaned")

    def test_cancelled_queued_run_is_never_dispatched(self):
        run_id = self.service.submit("owner", self.calendar, ["simulation:hang"], "queued")["run_id"]
        cancelled = self.service.cancel("owner", run_id)
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertFalse(self.service.process_next())
        self.assertFalse((self.service.root / "ledgers").exists())

    def test_foreign_cancel_is_rejected(self):
        run_id = self.service.submit("owner", self.calendar, ["simulation:hang"], "queued")["run_id"]
        with self.assertRaises(ServiceError) as raised:
            self.service.cancel("foreign", run_id)
        self.assertEqual(raised.exception.status, 404)

    def test_executor_limit_changes_invalidate_pending_context(self):
        run_id = self.service.submit("owner", self.storage, ["filesystem"], "context")["run_id"]
        self.service.executor = SubprocessExecutor(WorkerLimits(wall_seconds=3))
        self.service.process_next()
        run = self.service.get_run("owner", run_id)
        self.assertEqual(run["reason_code"], "execution_context_changed")
        self.assertFalse((self.root / "objects").exists())

    def test_worker_crash_is_not_a_provider_failure(self):
        run_id = self.service.submit("owner", self.calendar, ["simulation:crash"], "crash")["run_id"]
        self.service.process_next()
        run = self.service.get_run("owner", run_id)
        self.assertEqual(run["state"], "interrupted")
        self.assertEqual(run["reason_code"], "worker_crashed")
        self.assertEqual(run["candidates"], [])
        with closing(sqlite3.connect(self.service.root / "queue.sqlite3")) as db, db:
            self.assertIsNone(db.execute("SELECT result FROM jobs WHERE id=?", (run_id,)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
