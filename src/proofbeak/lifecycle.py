"""Evidence validity, tenant-scoped history/audit, and conservative local retention."""

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Lock

from proofbeak.models import canonical, fingerprint
from proofbeak.store import Store


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp needs an offset")
    return parsed


def redact_report(report):
    redacted = json.loads(canonical(report))
    redacted["test"] = {"id": report["test"]["id"], "redacted": True}
    for candidate in redacted["candidates"]:
        candidate["evidence"] = None
        candidate["raw_retained"] = False
    return redacted


class EvidenceLifecycle:
    def _init_lifecycle(self):
        self.operation_locks = {}
        self.operation_guard = Lock()
        with self._db() as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            for column in ("revoked_at", "raw_purged_at", "completed_at"):
                if column not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT")
            db.execute("""CREATE TABLE IF NOT EXISTS tombstones (
                tenant TEXT NOT NULL, id TEXT NOT NULL, key_hash TEXT NOT NULL,
                deleted_at TEXT NOT NULL, PRIMARY KEY(tenant,id), UNIQUE(tenant,key_hash))""")
            db.execute("""CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL, run_id TEXT,
                event TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL)""")
            db.execute("CREATE TABLE IF NOT EXISTS disabled_tokens (digest TEXT PRIMARY KEY)")
            db.execute("CREATE INDEX IF NOT EXISTS job_history ON jobs(tenant,created_at,id)")
            db.execute("CREATE INDEX IF NOT EXISTS evidence_lookup ON jobs(tenant,request_hash,state)")
            db.execute("CREATE INDEX IF NOT EXISTS job_dispatch ON jobs(state,created_at,id)")
            db.execute("CREATE INDEX IF NOT EXISTS tenant_audit ON audit(tenant,id)")

    @contextmanager
    def _operation(self, run_id):
        from proofbeak.service import ServiceError

        with self.operation_guard:
            lock = self.operation_locks.setdefault(run_id, Lock())
            if not lock.acquire(blocking=False):
                raise ServiceError("run_still_active", 409)
        try:
            yield
        finally:
            with self.operation_guard:
                lock.release()
                del self.operation_locks[run_id]

    def _audit(self, db, tenant, event, run_id=None, detail=None):
        db.execute("INSERT INTO audit (tenant,run_id,event,detail,created_at) VALUES (?,?,?,?,?)",
                   (tenant, run_id, event, canonical(detail or {}), datetime.now(UTC).isoformat()))

    def record_event(self, tenant, event, run_id=None, detail=None):
        with self._db() as db:
            self._audit(db, tenant, event, run_id, detail)

    def _is_deleted(self, tenant, run_id):
        with self._db() as db:
            return db.execute("SELECT 1 FROM tombstones WHERE tenant=? AND id=?", (tenant, run_id)).fetchone() is not None

    def _disabled(self, digest):
        with self._db() as db:
            return db.execute("SELECT 1 FROM disabled_tokens WHERE digest=?", (digest,)).fetchone() is not None

    def history(self, tenant, limit=25, before=None):
        from proofbeak.service import ServiceError

        if type(limit) is not int or not 1 <= limit <= 100:
            raise ServiceError("invalid_limit", 422)
        clause, parameters = "tenant=?", [tenant]
        if before:
            cursor = self._row(tenant, before)
            clause += " AND (created_at<? OR (created_at=? AND id<?))"
            parameters += [cursor["created_at"], cursor["created_at"], cursor["id"]]
        with self._db() as db:
            rows = db.execute(f"SELECT id FROM jobs WHERE {clause} ORDER BY created_at DESC,id DESC LIMIT ?",
                              [*parameters, limit + 1]).fetchall()
        items = [self.get_run(tenant, row["id"]) for row in rows[:limit]]
        return {"runs": items, "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None}

    def audit_events(self, tenant, limit=50, before=None):
        from proofbeak.service import ServiceError

        if type(limit) is not int or not 1 <= limit <= 100 or (before is not None and
                (type(before) is not int or not 1 <= before < 2 ** 63)):
            raise ServiceError("invalid_limit", 422)
        with self._db() as db:
            rows = db.execute("SELECT * FROM audit WHERE tenant=? AND id<? ORDER BY id DESC LIMIT ?",
                              (tenant, before or 2 ** 63 - 1, limit + 1)).fetchall()
        events = [{"id": row["id"], "run_id": row["run_id"], "event": row["event"],
                   "detail": json.loads(row["detail"]), "created_at": row["created_at"]} for row in rows[:limit]]
        return {"events": events, "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None}

    def validity(self, row, candidate, now=None):
        now = now or datetime.now(UTC)
        reasons = []
        if row["revoked_at"]:
            reasons.append("revoked")
        if row["raw_purged_at"]:
            reasons.append("raw_purged")
        elif candidate.get("evidence") is None:
            reasons.append("integrity_failed")
        if candidate.get("evidence_level") == "simulation" or not candidate.get("eligible_for_provider_comparison"):
            reasons.append("simulation")
        if candidate.get("verdict") not in {"pass", "fail"}:
            reasons.append("inconclusive")
        try:
            expires, observed = timestamp(candidate["expires_at"]), timestamp(candidate["observed_at"])
            if expires <= now:
                reasons.append("expired")
            if observed > now + timedelta(minutes=1):
                reasons.append("invalid_timestamp")
            if candidate.get("profile", {}).get("provider_version") == "unknown" and expires > observed + timedelta(hours=24):
                reasons.append("invalid_expiry")
        except (ValueError, TypeError, KeyError):
            reasons.append("invalid_timestamp")
        if candidate.get("evidence") is not None:
            try:
                intact = fingerprint(candidate["evidence"]) == candidate["evidence_hash"]
            except (ValueError, TypeError, KeyError):
                intact = False
            if not intact:
                reasons.append("integrity_failed")
        if not row["raw_purged_at"]:
            request = json.loads(row["request"])
            try:
                current = self._context(row["tenant"], request["test"], request["candidates"])
                if fingerprint(current) != fingerprint(json.loads(row["context"])):
                    reasons.append("context_changed")
            except Exception:
                reasons.append("context_unavailable")
        return {"reusable": not reasons, "reasons": sorted(set(reasons)), "checked_at": now.isoformat()}

    def lookup(self, tenant, spec, candidates, fresh=False):
        from proofbeak.service import ServiceError

        if type(fresh) is not bool:
            raise ServiceError("invalid_fresh_flag", 422)
        context = self._context(tenant, spec, candidates)
        digest = fingerprint({"request": {"test": spec, "candidates": candidates}, "context": context})
        if fresh:
            return {"reusable": False, "reason_code": "fresh_requested", "run_id": None}
        with self._db() as db:
            rows = db.execute("SELECT * FROM jobs WHERE tenant=? AND request_hash=? AND state='completed' "
                "AND revoked_at IS NULL AND raw_purged_at IS NULL ORDER BY created_at DESC,id DESC LIMIT 50",
                (tenant, digest)).fetchall()
        for row in rows:
            report = json.loads(row["result"])
            if report["candidates"] and all(self.validity(row, candidate)["reusable"] for candidate in report["candidates"]):
                self.record_event(tenant, "evidence_reused", row["id"])
                return {"reusable": True, "reason_code": "current_context_match", "run_id": row["id"],
                        "comparison": self.comparison(tenant, row["id"])}
        return {"reusable": False, "reason_code": "no_current_evidence", "run_id": None}

    def revoke(self, tenant, run_id):
        self._row(tenant, run_id)
        with self._db() as db:
            db.execute("UPDATE jobs SET revoked_at=COALESCE(revoked_at,?) WHERE tenant=? AND id=?",
                       (datetime.now(UTC).isoformat(), tenant, run_id))
            self._audit(db, tenant, "evidence_revoked", run_id)
        return self.get_run(tenant, run_id)

    def _ledger(self, run_id):
        from proofbeak.service import ServiceError

        path = self.root / "ledgers" / f"{run_id}.sqlite3"
        if not path.exists():
            return None, path
        try:
            return Store(path), path
        except ValueError:
            raise ServiceError("run_still_active", 409) from None

    def _erase_ledger(self, store, path):
        if store:
            store.erase()
            store.close()
            path.unlink()
            path.with_suffix(path.suffix + ".lock").unlink(missing_ok=True)

    def delete_run(self, tenant, run_id):
        from proofbeak.service import ServiceError

        self._row(tenant, run_id)
        with self._operation(run_id), self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE tenant=? AND id=?", (tenant, run_id)).fetchone()
            if row is None:
                raise ServiceError("not_found", 404)
            if row["state"] in {"queued", "running"}:
                raise ServiceError("run_still_active", 409)
            store, path = self._ledger(run_id)
            if store and store.pending():
                store.close()
                raise ServiceError("cleanup_required", 409)
            self._erase_ledger(store, path)
            db.execute("INSERT OR IGNORE INTO tombstones VALUES (?,?,?,?)", (
                tenant, run_id, fingerprint(row["client_key"]), datetime.now(UTC).isoformat()))
            db.execute("DELETE FROM jobs WHERE tenant=? AND id=?", (tenant, run_id))
            self._audit(db, tenant, "run_deleted", run_id)
        return {"run_id": run_id, "deleted": True, "idempotency_tombstone_retained": True}

    def _purge_raw(self, tenant, run_id, now):
        from proofbeak.service import ServiceError

        with self._operation(run_id), self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE tenant=? AND id=?", (tenant, run_id)).fetchone()
            if row is None or row["state"] in {"queued", "running"} or row["raw_purged_at"]:
                return False
            store, _ = self._ledger(run_id)
            try:
                if store:
                    store.purge_raw()
                request = json.loads(row["request"])
                request["test"] = {"id": request["test"]["id"], "redacted": True}
                report = redact_report(json.loads(row["result"])) if row["result"] else None
                db.execute("UPDATE jobs SET request=?,result=?,raw_purged_at=? WHERE tenant=? AND id=?",
                           (canonical(request), canonical(report) if report else None, now.isoformat(), tenant, run_id))
                self._audit(db, tenant, "raw_evidence_purged", run_id)
            except (OSError, ValueError):
                raise ServiceError("retention_failed", 500) from None
            finally:
                if store:
                    store.close()
        return True

    def maintain(self, now=None, batch=100):
        from proofbeak.service import ServiceError

        now = now or datetime.now(UTC)
        if now.tzinfo is None or type(batch) is not int or not 1 <= batch <= 1000:
            raise ValueError("maintenance needs an aware timestamp and a bounded batch")
        now = now.astimezone(UTC)
        summary_cutoff, raw_cutoff = now - timedelta(days=30), now - timedelta(days=7)
        counts = {"raw_purged": 0, "runs_deleted": 0, "deferred": 0}
        with self._db() as db:
            rows = db.execute("SELECT * FROM jobs WHERE state NOT IN ('queued','running') "
                "AND COALESCE(completed_at,created_at)<? AND (raw_purged_at IS NULL OR "
                "COALESCE(completed_at,created_at)<?) ORDER BY created_at LIMIT ?",
                (raw_cutoff.isoformat(), summary_cutoff.isoformat(), batch)).fetchall()
        for row in rows:
            try:
                completed = timestamp(row["completed_at"] or row["created_at"])
                if self._purge_raw(row["tenant"], row["id"], now):
                    counts["raw_purged"] += 1
                if completed < summary_cutoff:
                    self.delete_run(row["tenant"], row["id"])
                    counts["runs_deleted"] += 1
            except ServiceError:
                counts["deferred"] += 1
        with self._db() as db:
            db.execute("DELETE FROM audit WHERE created_at<?", (summary_cutoff.isoformat(),))
        # Secure-delete is enabled on every connection; compact freed cells after a batch.
        if counts["raw_purged"] or counts["runs_deleted"]:
            with self._db() as db:
                db.execute("VACUUM")
        return counts

    def purge_tenant(self, tenant):
        from proofbeak.service import ServiceError

        if self.lock is None or self.worker is not None:
            raise ServiceError("offline_maintenance_required", 409)
        if tenant not in self.tenants:
            raise ServiceError("not_found", 404)
        with self._db() as db:
            rows = db.execute("SELECT id FROM jobs WHERE tenant=?", (tenant,)).fetchall()
        opened = []
        try:
            for row in rows:
                store, path = self._ledger(row["id"])
                opened.append((store, path))
                if store and store.pending():
                    raise ServiceError("cleanup_required", 409)
            for store, path in opened:
                self._erase_ledger(store, path)
            opened.clear()
            with self._db() as db:
                db.execute("DELETE FROM jobs WHERE tenant=?", (tenant,))
                db.execute("DELETE FROM tombstones WHERE tenant=?", (tenant,))
                db.execute("DELETE FROM audit WHERE tenant=?", (tenant,))
                db.execute("INSERT OR IGNORE INTO disabled_tokens VALUES (?)", (self.tenants[tenant].token_sha256,))
            directory = self.root / "provider-data" / tenant
            if directory.exists():
                shutil.rmtree(directory)
            with self._db() as db:
                db.execute("VACUUM")
        finally:
            for store, _ in opened:
                if store:
                    store.close()
        return {"tenant_purged": True, "credential_disabled": True}
