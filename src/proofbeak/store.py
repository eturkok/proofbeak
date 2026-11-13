"""Transactional reservations and durable cleanup intent for a local CLI owner."""

import fcntl
import json
import os
import sqlite3
import stat
from pathlib import Path
from uuid import uuid4

from proofbeak.models import canonical


class IdempotencyConflict(ValueError):
    pass


class Store:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path.with_suffix(path.suffix + ".lock"),
                             os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self.lock = os.fdopen(descriptor, "a+")
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("store lock must be a regular file")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise ValueError("another local worker owns this store; retry when it finishes") from None
        except BaseException:
            self.lock.close()
            raise
        self.db = None
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError("store must be a regular file")
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
            self.db = sqlite3.connect(path, timeout=10)
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA secure_delete=ON")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    client_key TEXT UNIQUE NOT NULL,
                    request_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result TEXT
                );
                CREATE TABLE IF NOT EXISTS resources (
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    adapter TEXT NOT NULL,
                    profile_hash TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY (run_id, adapter)
                );
            """)
        except BaseException:
            if self.db is not None:
                self.db.close()
            self.lock.close()
            raise

    def reserve(self, key: str, request_hash: str) -> tuple[str, dict | None]:
        if not key or len(key) > 128:
            raise ValueError("idempotency key must contain 1 to 128 characters")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT id, request_hash, state, result FROM runs WHERE client_key=?", (key,)
            ).fetchone()
            if row:
                if row[1] != request_hash:
                    raise IdempotencyConflict("key already belongs to a different request")
                return row[0], json.loads(row[3]) if row[3] else {
                    "run_id": row[0], "state": row[2], "reason_code": "recovery_required",
                    "candidates": [],
                }
            run_id = uuid4().hex
            self.db.execute("INSERT INTO runs VALUES (?, ?, ?, 'running', NULL)",
                            (run_id, key, request_hash))
            return run_id, None

    def track(self, run_id, adapter, profile_hash, resource):
        with self.db:
            self.db.execute("INSERT INTO resources VALUES (?, ?, ?, ?, 'pending')",
                            (run_id, adapter, profile_hash, canonical(resource)))

    def cleaned(self, run_id, adapter, status):
        with self.db:
            self.db.execute("UPDATE resources SET status=? WHERE run_id=? AND adapter=?",
                            (status, run_id, adapter))

    def pending(self):
        rows = self.db.execute(
            "SELECT run_id, adapter, profile_hash, resource, status FROM resources "
            "WHERE status != 'cleaned'").fetchall()
        return [{"run_id": row[0], "adapter": row[1], "profile_hash": row[2],
                 "resource": json.loads(row[3]), "cleanup_status": row[4]} for row in rows]

    def finish(self, run_id, result):
        with self.db:
            self.db.execute("UPDATE runs SET state='completed', result=? WHERE id=?",
                            (canonical(result), run_id))

    def purge_raw(self):
        from proofbeak.lifecycle import redact_report

        with self.db:
            rows = self.db.execute("SELECT id,result FROM runs WHERE result IS NOT NULL").fetchall()
            for run_id, result in rows:
                self.db.execute("UPDATE runs SET result=? WHERE id=?",
                                (canonical(redact_report(json.loads(result))), run_id))
        self.db.execute("VACUUM")

    def erase(self):
        if self.pending():
            raise ValueError("cleanup is required before erasing a resource ledger")
        with self.db:
            self.db.execute("DELETE FROM resources")
            self.db.execute("DELETE FROM runs")
        self.db.execute("VACUUM")

    def close(self):
        self.db.close()
        self.lock.close()
