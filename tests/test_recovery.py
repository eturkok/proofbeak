"""Actual SQLite/filesystem snapshots and conservative restoration, including rollback fencing."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.recovery import backup, restore
from proofbeak.service import ServiceError, Tenant, TrialService
from proofbeak.store import Store


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.snapshot = self.root / "snapshot"
        self.restored = self.root / "restored"
        self.tenant = Tenant("demo", hashlib.sha256(b"a" * 40).hexdigest(), {
            "filesystem": lambda: FilesystemAdapter(self.state / "provider-data/demo/objects")})
        self.service = self.open_service(self.state)
        self.spec = json.loads((Path(__file__).resolve().parents[1] /
                               "examples/storage.put_same_key.v1.json").read_text())

    def open_service(self, root):
        service = TrialService(root, [self.tenant])
        service.start(background=False)
        self.addCleanup(service.close)
        return service

    def completed(self, key):
        run_id = self.service.submit("demo", self.spec, ["filesystem"], key)["run_id"]
        self.service.process_next()
        return run_id

    def snapshot_state(self):
        self.service.close()
        return backup(self.state, self.snapshot)

    def test_restore_preserves_evidence_tombstones_and_fences_post_snapshot_retries(self):
        completed = self.completed("historical")
        original = self.service.evidence("demo", completed + ":0")
        deleted = self.completed("deleted")
        self.service.delete_run("demo", deleted)
        queued = self.service.submit("demo", self.spec, ["filesystem"], "queued")["run_id"]
        running = self.service.submit("demo", self.spec, ["filesystem"], "running")["run_id"]
        with self.service._db() as db:
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (running,))
        info = self.snapshot_state()
        self.assertGreater(info["bytes"], 0)
        # This accepted write is deliberately absent from the snapshot.
        self.service.start(background=False)
        self.service.process_next()  # Drain the original queued job.
        lost = self.completed("lost-after-snapshot")
        self.service.close()
        restored = restore(self.snapshot, self.restored)
        service = self.open_service(self.restored)
        for name, value in original.items():
            if name != "validity":
                self.assertEqual(service.evidence("demo", completed + ":0")[name], value)
        self.assertEqual(service.submit("demo", self.spec, ["filesystem"], "historical")["run_id"], completed)
        for key in ("lost-after-snapshot", "unseen-old-key"):
            with self.assertRaises(ServiceError) as raised:
                service.submit("demo", self.spec, ["filesystem"], key)
            self.assertEqual(raised.exception.code, "restore_key_prefix_required")
        self.assertNotIn(lost, [row["run_id"] for row in service.history("demo")["runs"]])
        with self.assertRaises(ServiceError) as raised:
            service.submit("demo", self.spec, ["filesystem"], "deleted")
        self.assertEqual(raised.exception.code, "run_deleted")
        for run_id in (queued, running):
            self.assertEqual(service.get_run("demo", run_id)["state"], "interrupted")
            self.assertEqual(service.get_run("demo", run_id)["reason_code"], "state_restored")
        self.assertFalse(service.process_next())
        prefix = restored["idempotency_key_prefix"]
        self.assertEqual(service.packs("demo")["idempotency_key_prefix"], prefix)
        fresh = service.submit("demo", self.spec, ["filesystem"], prefix + "explicit-new-trial")["run_id"]
        self.assertTrue(service.process_next())
        self.assertEqual(service.get_run("demo", fresh)["candidates"][0]["verdict"], "pass")
        self.assertIn("state_restored", json.dumps(service.audit_events("demo")))

    def test_database_trigger_blocks_older_coordinators_that_do_not_read_prefix(self):
        self.completed("historical")
        self.snapshot_state()
        restore(self.snapshot, self.restored)
        with closing(sqlite3.connect(self.restored / "queue.sqlite3")) as db, db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "restore_key_prefix_required"):
                db.execute("INSERT INTO jobs (id,tenant,client_key,request_hash,request,context,state,created_at) "
                           "VALUES (?,?,?,?,?,?,?,?)", ("f" * 32, "demo", "lost", "hash", "{}", "{}", "queued", "now"))

    def test_each_restore_issues_a_new_persistent_prefix(self):
        self.snapshot_state()
        first = restore(self.snapshot, self.restored)
        second = restore(self.snapshot, self.root / "another")
        self.assertNotEqual(first["idempotency_key_prefix"], second["idempotency_key_prefix"])
        service = self.open_service(self.restored)
        self.assertEqual(service.key_prefix(), first["idempotency_key_prefix"])

    def test_active_coordinator_and_ledger_prevent_snapshot(self):
        with self.assertRaisesRegex(ValueError, "offline_state_required"):
            backup(self.state, self.snapshot)
        self.assertFalse(self.snapshot.exists())
        run_id = self.completed("owned-ledger")
        self.service.close()
        store = Store(self.state / "ledgers" / (run_id + ".sqlite3"))
        try:
            with self.assertRaisesRegex(ValueError, "offline_state_required"):
                backup(self.state, self.snapshot)
        finally:
            store.close()
        self.assertFalse(self.snapshot.exists())

    def test_existing_destinations_are_never_overwritten(self):
        self.snapshot_state()
        manifest = (self.snapshot / "manifest.json").read_bytes()
        with self.assertRaises(FileExistsError):
            backup(self.state, self.snapshot)
        self.assertEqual((self.snapshot / "manifest.json").read_bytes(), manifest)
        self.restored.mkdir()
        sentinel = self.restored / "keep"
        sentinel.write_text("existing-state")
        with self.assertRaises(FileExistsError):
            restore(self.snapshot, self.restored)
        self.assertEqual(sentinel.read_text(), "existing-state")

    def test_corrupt_or_unlisted_snapshot_files_fail_before_target_creation(self):
        self.snapshot_state()
        database = self.snapshot / "queue.sqlite3"
        database.write_bytes(database.read_bytes() + b"corrupt")
        with self.assertRaisesRegex(ValueError, "snapshot_checksum_failed"):
            restore(self.snapshot, self.restored)
        self.assertFalse(self.restored.exists())

    def test_traversal_and_symlinks_are_rejected(self):
        self.snapshot_state()
        manifest = self.snapshot / "manifest.json"
        original = manifest.read_text()
        data = json.loads(original)
        data["files"][0]["path"] = "../outside"
        manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            restore(self.snapshot, self.restored)
        manifest.write_text(original)
        (self.snapshot / "outside-link").symlink_to(self.root)
        with self.assertRaises(ValueError):
            restore(self.snapshot, self.restored)
        self.assertFalse(self.restored.exists())

    def test_partial_restore_is_cleaned_and_incomplete_state_cannot_start(self):
        self.snapshot_state()
        with patch("proofbeak.recovery.private_file", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                restore(self.snapshot, self.restored)
        self.assertFalse(self.restored.exists())
        self.restored.mkdir(mode=0o700)
        (self.restored / ".restore-incomplete").write_text("incomplete")
        with self.assertRaisesRegex(ValueError, "restore is incomplete"):
            TrialService(self.restored, [self.tenant])
        self.assertFalse((self.restored / "queue.sqlite3").exists())

    def test_snapshot_and_restored_entries_are_private(self):
        self.completed("private")
        self.snapshot_state()
        restore(self.snapshot, self.restored)
        for root in (self.snapshot, self.restored):
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            for path in root.rglob("*"):
                self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_snapshot_restores_empty_owned_filesystem_directory(self):
        path = self.state / "provider-data/demo/objects" / ("proofbeak-" + "a" * 32)
        path.mkdir(parents=True)
        self.snapshot_state()
        restore(self.snapshot, self.restored)
        self.assertTrue((self.restored / path.relative_to(self.state)).is_dir())

    def test_symlink_in_state_and_nested_snapshot_destination_are_rejected(self):
        self.service.close()
        with self.assertRaises(ValueError):
            backup(self.state, self.state / "backup")
        (self.state / "link").symlink_to(self.root)
        with self.assertRaises(ValueError):
            backup(self.state, self.snapshot)
        self.assertFalse(self.snapshot.exists())

    def test_unknown_database_schema_is_rejected(self):
        self.service.close()
        with closing(sqlite3.connect(self.service.database)) as db, db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(ValueError, "unsupported_state_schema"):
            backup(self.state, self.snapshot)
        self.assertFalse(self.snapshot.exists())

    def test_incompatible_columns_and_oversized_state_are_rejected(self):
        self.service.close()
        with patch("proofbeak.recovery.MAX_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "snapshot_size_limit"):
                backup(self.state, self.snapshot)
        self.assertFalse(self.snapshot.exists())
        with closing(sqlite3.connect(self.service.database)) as db, db:
            db.execute("ALTER TABLE jobs RENAME COLUMN client_key TO incompatible_key")
        with self.assertRaisesRegex(ValueError, "unsupported_state_schema"):
            backup(self.state, self.snapshot)
        self.assertFalse(self.snapshot.exists())
