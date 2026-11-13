"""Bounded repeated-use qualification; synthetic data, actual workers and optional HTTP providers."""

import argparse
import hashlib
import json
import os
import tempfile
import time
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.recovery import backup, restore
from proofbeak.service import ServiceError, Tenant, TrialService
from webdav_fixtures import WebDAVFixtures

ROOT = Path(__file__).resolve().parents[1]


def fd_count():
    return len(os.listdir("/proc/self/fd")) if Path("/proc/self/fd").is_dir() else None


def qualification(iterations, fixture=None):
    started, fds_before = time.monotonic(), fd_count()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        state = root / "state"
        names = ["webdav:wsgidav", "webdav:apache"] if fixture else ["filesystem"]
        tenants = [Tenant(owner, hashlib.sha256((owner * 40).encode()).hexdigest(),
            {name: (lambda name=name, owner=owner: (
                next(a for a in fixture.adapters() if a.name == name) if fixture
                else FilesystemAdapter(state / "provider-data" / owner / "objects"))) for name in names})
            for owner in ("a", "b")]

        def opened():
            service = TrialService(state, tenants)
            service.start(background=False)
            return service

        service = opened()
        completed, deleted, cancellations, restarts, restores = [], 0, 0, 0, 0
        try:
            for index in range(iterations):
                owner = "ab"[index % 2]
                spec = json.loads((ROOT / "examples/storage.put_same_key.v1.json").read_text())
                spec["fixture"]["content"] = "Synthetic repeated-use observation " + str(index)
                key = service.key_prefix() + f"soak-{index:04d}"
                with ThreadPoolExecutor(max_workers=8) as pool:
                    submitted = list(pool.map(lambda _: service.submit(owner, spec, names, key), range(8)))
                assert len({run["run_id"] for run in submitted}) == 1, "parallel retries reserved multiple jobs"
                run_id = submitted[0]["run_id"]
                assert service.process_next()
                report = service.comparison(owner, run_id)
                assert report["state"] == "completed"
                assert all(row["verdict"] == "pass" and row["cleanup_status"] == "cleaned"
                           for row in report["candidates"])
                again = service.submit(owner, spec, names, key)
                assert again["run_id"] == run_id
                assert not service.process_next(), "a completed retry created work"
                try:
                    service.evidence("ab"[1 - index % 2], run_id + ":0")
                    raise AssertionError("foreign evidence became visible")
                except ServiceError as error:
                    assert error.code == "not_found"
                assert not service.executor.active, "a worker was left active"
                assert not service.operation_locks, "operation bookkeeping leaked"
                completed.append({"run_id": run_id, "owner": owner,
                    "evidence_hashes": [c["evidence_hash"] for c in report["candidates"]],
                    "observed_at": [c["observed_at"] for c in report["candidates"]]})
                if index % 10 == 3:
                    service.delete_run(owner, run_id)
                    completed[-1]["deleted"] = True
                    deleted += 1
                    try:
                        service.submit(owner, spec, names, key)
                        raise AssertionError("deleted key created new work")
                    except ServiceError as error:
                        assert error.code == "run_deleted"
                if index % 10 == 5:
                    cancelled = service.submit(owner, spec, names, service.key_prefix() + f"cancel-{index}")["run_id"]
                    assert service.cancel(owner, cancelled)["state"] == "cancelled"
                    assert not service.process_next()
                    cancellations += 1
                if index == iterations // 2:
                    service.close()
                    backup(state, root / "snapshot")
                    state.rename(root / "retired")
                    restore(root / "snapshot", state)
                    service = opened()
                    assert service.key_prefix()
                    restores += 1
                elif index % 10 == 8:
                    service.close()
                    service = opened()
                    restarts += 1
                if index % 10 == 8 or index == iterations // 2:
                    retained = next(row for row in reversed(completed) if not row.get("deleted"))
                    historical = service.comparison(retained["owner"], retained["run_id"])
                    assert [c["evidence_hash"] for c in historical["candidates"]] == retained["evidence_hashes"]
                    assert [c["observed_at"] for c in historical["candidates"]] == retained["observed_at"]
                    assert not service.process_next()
            with service._db() as db:
                events = dict(db.execute("SELECT event,COUNT(*) FROM audit GROUP BY event"))
                assert events["run_submitted"] == iterations + cancellations
                assert events["run_started"] == iterations
                assert events["run_completed"] == iterations
                assert db.execute("SELECT COUNT(*) FROM jobs WHERE state='completed'").fetchone()[0] == iterations - deleted
                assert db.execute("SELECT COUNT(*) FROM tombstones").fetchone()[0] == deleted
        finally:
            service.close()
        journals = fixture.mutations() if fixture else None
        if journals:
            for rows in journals.values():
                assert len(rows) == 5 * iterations, "retry/cancellation/recovery changed provider mutation count"
                assert Counter(r["method"] for r in rows) == {
                    "MKCOL": iterations, "PUT": 3 * iterations, "DELETE": iterations}
                assert all(200 <= row["status"] < 300 for row in rows)
        assert len({row["run_id"] for row in completed}) == iterations
        fds_after = fd_count()
        if fds_before is not None:
            assert fds_after <= fds_before + 4, "open file descriptors accumulated"
        return {"qualification": "passed", "iterations": iterations, "providers": names,
            "parallel_submissions_per_key": 8, "completed_trials": iterations,
            "cancelled_before_dispatch": cancellations, "deleted_with_tombstones": deleted,
            "coordinator_restarts": restarts, "verified_restores": restores,
            "provider_mutations_per_provider": 5 * iterations if fixture else None,
            "unexpected_provider_mutations": 0 if fixture else None,
            "file_descriptors_before": fds_before, "file_descriptors_after": fds_after,
            "active_workers_after_trials": 0, "operation_locks_after_trials": 0,
            "wall_seconds": round(time.monotonic() - started, 3), "runs": completed,
            "limits": ["A bounded synthetic repeated-use check, not a long-duration uptime study.",
                       "No model or human pilot was invoked; HTTP counts are not disk-write counts."]}


class HttpQualification(WebDAVFixtures, unittest.TestCase):
    iterations = 30
    summary = None

    def runTest(self):
        type(self).summary = qualification(self.iterations, self)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=60)
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("reports/stability-soak.json"))
    args = parser.parse_args()
    if not 20 <= args.iterations <= 1000:
        parser.error("iterations must be between 20 and 1000")
    if args.http:
        HttpQualification.iterations = args.iterations
        outcome = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([HttpQualification()]))
        if not outcome.wasSuccessful() or outcome.skipped:
            raise SystemExit(1)
        result = HttpQualification.summary
    else:
        result = qualification(args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "runs"}))
