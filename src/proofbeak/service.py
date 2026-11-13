"""Tenant-bound application service and a durable, single-worker local queue."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

from proofbeak.executor import SubprocessExecutor
from proofbeak.models import canonical, fingerprint
from proofbeak.lifecycle import EvidenceLifecycle
from proofbeak.observer import OBSERVER_VERSION
from proofbeak.packs import builtins
from proofbeak.recovery import file_lock


class ServiceError(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Tenant:
    id: str
    token_sha256: str
    providers: dict


class TrialService(EvidenceLifecycle):
    def __init__(self, root, tenants, pending_limit=8, executor=None):
        self.root = Path(root).resolve()
        if (self.root / ".restore-incomplete").exists():
            raise ValueError("state restore is incomplete")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.stat().st_mode & 0o077:
            raise ValueError("service state directory must be private to its owner (mode 0700)")
        self.tenants = {tenant.id: tenant for tenant in tenants}
        if not 1 <= len(tenants) <= 32 or len(self.tenants) != len(tenants):
            raise ValueError("configure one to 32 distinct tenants")
        hashes = set()
        for tenant in tenants:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant.id) or not re.fullmatch(
                r"[a-f0-9]{64}", tenant.token_sha256
            ) or tenant.token_sha256 in hashes or not tenant.providers:
                raise ValueError("invalid tenant identity, token hash, or provider allowlist")
            hashes.add(tenant.token_sha256)
        if type(pending_limit) is not int or not 1 <= pending_limit <= 100:
            raise ValueError("pending_limit must be between one and 100")
        self.pending_limit = pending_limit
        self.executor = executor or SubprocessExecutor()
        self.database = self.root / "queue.sqlite3"
        self.lock = None
        self.worker = None
        self.stop = Event()
        self.wake = Event()
        self.last_maintenance = 0
        self.maintenance_deferred = False
        self.coordinator_failure = None
        # Schema initialization must respect the same offline/worker exclusion as snapshots.
        try:
            with file_lock(self.root / "worker.lock"):
                with self._db() as db:
                    db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY, tenant TEXT NOT NULL, client_key TEXT NOT NULL,
                        request_hash TEXT NOT NULL, request TEXT NOT NULL, context TEXT NOT NULL,
                        state TEXT NOT NULL, result TEXT, reason TEXT, recovery TEXT,
                        created_at TEXT NOT NULL, UNIQUE(tenant, client_key))""")
                self._init_lifecycle()
        except ValueError as error:
            if str(error) == "offline_state_required":
                raise RuntimeError("another service worker owns this state directory") from None
            raise

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA secure_delete=ON")
            with db:
                yield db
        finally:
            db.close()

    def start(self, background=True):
        if self.lock is not None:
            raise RuntimeError("service already started")
        lock = (self.root / "worker.lock").open("a+")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError("another service worker owns this state directory") from None
        self.lock = lock
        # Holding the process lock establishes that these jobs have no surviving owner.
        # Never replay them: a provider write might have completed before the process died.
        try:
            with self._db() as db:
                db.execute("UPDATE jobs SET state='interrupted', reason='recovery_required' "
                           "WHERE state='running'")
            self.stop.clear()
            self.coordinator_failure = None
            self.executor.reset()
            if background:
                self.worker = Thread(target=self._loop, name="proofbeak-worker", daemon=False)
                self.worker.start()
        except BaseException:
            self.lock.close()
            self.lock = None
            raise

    def close(self):
        self.stop.set()
        self.wake.set()
        self.executor.cancel_all()
        if self.worker:
            self.worker.join(timeout=5)
            if self.worker.is_alive():
                raise RuntimeError("worker is still draining; exclusive ownership retained")
            self.worker = None
        if self.lock:
            self.lock.close()
            self.lock = None

    def authenticate(self, token):
        if not isinstance(token, str) or not 32 <= len(token) <= 256:
            raise ServiceError("unauthorized", 401)
        digest = hashlib.sha256(token.encode()).hexdigest()
        match = None
        for tenant in self.tenants.values():
            if hmac.compare_digest(digest, tenant.token_sha256):
                match = tenant.id
        if match is None:
            raise ServiceError("unauthorized", 401)
        if self._disabled(digest):
            raise ServiceError("unauthorized", 401)
        return match

    def _adapters(self, tenant_id, candidates):
        tenant = self.tenants.get(tenant_id)
        if tenant is None or self._disabled(tenant.token_sha256):
            raise ServiceError("unauthorized", 401)
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= 4 \
                or any(not isinstance(c, str) for c in candidates) or len(set(candidates)) != len(candidates):
            raise ServiceError("invalid_candidates", 422)
        if any(name not in tenant.providers for name in candidates):
            raise ServiceError("provider_not_allowed", 403)
        try:
            return [tenant.providers[name]() for name in candidates]
        except (ValueError, OSError):
            raise ServiceError("provider_not_configured", 422) from None

    def _context(self, tenant, spec, candidates):
        try:
            pack, _ = builtins().prepare(spec)
        except (ValueError, TypeError, RecursionError):
            raise ServiceError("invalid_trial", 422) from None
        adapters = self._adapters(tenant, candidates)
        if any(pack.id not in a.supported_trials for a in adapters):
            raise ServiceError("unsupported_trial_for_provider", 422)
        try:
            self.executor.validate(adapters)
        except ValueError:
            raise ServiceError("unsupported_worker_adapter", 422) from None
        return {"pack_id": pack.id, "pack_version": pack.version,
                "executor": self.executor.context(),
                "evidence_policy_version": "1",
                "observer_version": OBSERVER_VERSION,
                "providers": [{"name": a.name, "version": a.version, "profile": a.profile,
                               "evidence_level": a.evidence_level} for a in adapters]}

    def submit(self, tenant, spec, candidates, key):
        if not self.available:
            raise ServiceError("service_unavailable", 503)
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ServiceError("invalid_idempotency_key", 422)
        with self._db() as db:
            if db.execute("SELECT 1 FROM tombstones WHERE tenant=? AND key_hash=?",
                          (tenant, fingerprint(key))).fetchone():
                raise ServiceError("run_deleted", 410)
        try:
            request = canonical({"test": spec, "candidates": candidates})
            if len(request.encode()) > 65536:
                raise ServiceError("request_too_large", 413)
        except (ValueError, TypeError, RecursionError):
            raise ServiceError("invalid_trial", 422) from None
        context = self._context(tenant, spec, candidates)
        request_hash = fingerprint({"request": json.loads(request), "context": context})
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            # Deletion may commit while provider context is being validated. Check again
            # under the same write transaction that reserves the key.
            if db.execute("SELECT 1 FROM tombstones WHERE tenant=? AND key_hash=?",
                          (tenant, fingerprint(key))).fetchone():
                raise ServiceError("run_deleted", 410)
            row = db.execute("SELECT * FROM jobs WHERE tenant=? AND client_key=?", (tenant, key)).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise ServiceError("idempotency_conflict", 409)
                return {"run_id": row["id"], "state": row["state"]}
            prefix = self.key_prefix(db)
            if prefix and not key.startswith(prefix):
                raise ServiceError("restore_key_prefix_required", 409)
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE tenant=? "
                               "AND state IN ('queued','running')", (tenant,)).fetchone()[0]
            if count >= self.pending_limit:
                raise ServiceError("tenant_queue_full", 429)
            run_id = uuid4().hex
            db.execute("INSERT INTO jobs (id,tenant,client_key,request_hash,request,context,state,result,reason,recovery,created_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, NULL, ?)",
                       (run_id, tenant, key, request_hash, request, canonical(context),
                        datetime.now(UTC).isoformat()))
            self._audit(db, tenant, "run_submitted", run_id, {"request_hash": request_hash})
        self.wake.set()
        return {"run_id": run_id, "state": "queued"}

    def _row(self, tenant, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ServiceError("not_found", 404)
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE tenant=? AND id=?", (tenant, run_id)).fetchone()
        if row is None:
            if self._is_deleted(tenant, run_id):
                raise ServiceError("run_deleted", 410)
            raise ServiceError("not_found", 404)
        return row

    def get_run(self, tenant, run_id):
        row = self._row(tenant, run_id)
        candidates = []
        if row["result"]:
            for index, item in enumerate(json.loads(row["result"])["candidates"]):
                candidates.append({"provider": item["provider"], "verdict": item["verdict"],
                                   "reason_code": item["reason_code"], "cleanup_status": item["cleanup_status"],
                                   "evidence_ref": f"{run_id}:{index}"})
        return {"run_id": run_id, "state": row["state"], "reason_code": row["reason"],
                "created_at": row["created_at"], "candidates": candidates,
                "revoked_at": row["revoked_at"], "raw_purged_at": row["raw_purged_at"],
                "cleanup_recovery": json.loads(row["recovery"]) if row["recovery"] else None}

    def comparison(self, tenant, run_id):
        row = self._row(tenant, run_id)
        if row["result"] is None:
            raise ServiceError("result_not_available", 409)
        report = json.loads(row["result"])
        for index, item in enumerate(report["candidates"]):
            item["validity"] = self.validity(row, item)
            item.pop("evidence", None)
            item["evidence_ref"] = f"{run_id}:{index}"
        report["revoked_at"] = row["revoked_at"]
        return report

    def evidence(self, tenant, evidence_id):
        if not isinstance(evidence_id, str) or not re.fullmatch(r"[a-f0-9]{32}:[0-3]", evidence_id):
            raise ServiceError("not_found", 404)
        run_id, index = evidence_id.split(":")
        row = self._row(tenant, run_id)
        if row["result"] is None:
            raise ServiceError("result_not_available", 409)
        candidates = json.loads(row["result"])["candidates"]
        if int(index) >= len(candidates):
            raise ServiceError("not_found", 404)
        candidate = candidates[int(index)]
        validity = self.validity(row, candidate)
        if "integrity_failed" in validity["reasons"]:
            self.record_event(tenant, "evidence_integrity_failed", run_id)
            raise ServiceError("evidence_integrity_failed", 409)
        if row["raw_purged_at"]:
            raise ServiceError("raw_evidence_purged", 410)
        self.record_event(tenant, "evidence_read", run_id, {"candidate": int(index)})
        return {"evidence_id": evidence_id, "run_id": run_id, **candidate, "validity": validity}

    def cleanup(self, tenant, run_id):
        self._row(tenant, run_id)
        with self._operation(run_id):
            return self._cleanup_job(tenant, run_id)

    def _cleanup_job(self, tenant, run_id):
        row = self._row(tenant, run_id)
        if row["state"] in {"queued", "running"}:
            raise ServiceError("run_still_active", 409)
        ledger = self.root / "ledgers" / f"{run_id}.sqlite3"
        outcomes = []
        if ledger.exists():
            request = json.loads(row["request"])
            adapters = self._adapters(tenant, request["candidates"])
            result = self.executor.cleanup(adapters, ledger)
            if result.reason:
                code = "cleanup_timed_out" if result.reason == "trial_timed_out" else result.reason
                status = 409 if code == "run_still_active" else (429 if code == "cleanup_busy" else 504)
                raise ServiceError(code, status)
            outcomes = result.payload["outcomes"]
        with self._db() as db:
            db.execute("UPDATE jobs SET recovery=? WHERE id=? AND tenant=?",
                       (canonical(outcomes), run_id, tenant))
            self._audit(db, tenant, "cleanup_retried", run_id, {"statuses": [o["status"] for o in outcomes]})
        return {"run_id": run_id, "cleanup_recovery": outcomes}

    def cancel(self, tenant, run_id):
        self._row(tenant, run_id)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE tenant=? AND id=?", (tenant, run_id)).fetchone()
            if row["state"] == "queued":
                db.execute("UPDATE jobs SET state='cancelled', reason='worker_cancelled' WHERE id=?",
                           (run_id,))
                self._audit(db, tenant, "run_cancelled", run_id)
            elif row["state"] == "running":
                self.executor.cancel(run_id)
                self._audit(db, tenant, "cancellation_requested", run_id)
        return self.get_run(tenant, run_id)

    def packs(self, tenant):
        if tenant not in self.tenants:
            raise ServiceError("unauthorized", 401)
        capabilities = {}
        for name, factory in self.tenants[tenant].providers.items():
            try:
                adapter = factory()
                capabilities[name] = {"configured": True, "supported_trials": sorted(adapter.supported_trials)}
            except (ValueError, OSError):
                capabilities[name] = {"configured": False, "supported_trials": []}
        return {"packages": builtins().describe(), "providers": sorted(self.tenants[tenant].providers),
                "provider_capabilities": capabilities, "idempotency_key_prefix": self.key_prefix()}

    def process_next(self):
        if self.lock is None:
            raise RuntimeError("start the worker before claiming jobs")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at, id LIMIT 1").fetchone()
            if row is None:
                return False
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (row["id"],))
            self._audit(db, row["tenant"], "run_started", row["id"])
        report, reason = None, None
        try:
            request = json.loads(row["request"])
            context = self._context(row["tenant"], request["test"], request["candidates"])
            if fingerprint(context) != fingerprint(json.loads(row["context"])):
                raise ServiceError("execution_context_changed", 409)
            adapters = self._adapters(row["tenant"], request["candidates"])
            result = self.executor.run(request["test"], adapters,
                self.root / "ledgers" / f'{row["id"]}.sqlite3', row["id"])
            reason = result.reason
            if result.payload:
                report = result.payload["report"]
                report["execution_run_id"] = report["run_id"]
                report["run_id"] = row["id"]
        except ServiceError as exc:
            reason = exc.code
        except Exception:
            # Provider exceptions may contain secrets. Only a stable code is persisted or returned.
            reason = "execution_interrupted"
        with self._db() as db:
            db.execute("UPDATE jobs SET state=?, result=?, reason=?, completed_at=? WHERE id=?", (
                "completed" if report else ("cancelled" if reason == "worker_cancelled" else "interrupted"),
                canonical(report) if report else None,
                reason, datetime.now(UTC).isoformat(), row["id"]))
            self._audit(db, row["tenant"], "run_completed" if report else "run_interrupted", row["id"],
                        {"reason_code": reason})
        return True

    @property
    def available(self):
        return self.lock is not None and not self.stop.is_set() and self.coordinator_failure is None \
            and (self.worker is None or self.worker.is_alive())

    def _loop(self):
        while not self.stop.is_set():
            now = time.monotonic()
            interval = 60 if self.maintenance_deferred else 3600
            if self.last_maintenance == 0 or now - self.last_maintenance >= interval:
                try:
                    maintenance = self.maintain()
                    self.maintenance_deferred = bool(maintenance.get("deferred", 0))
                except (OSError, sqlite3.Error, ServiceError):
                    # A temporary retention failure must not kill the dispatcher or
                    # print potentially credential-bearing exception messages.
                    self.maintenance_deferred = True
                self.last_maintenance = now
            try:
                processed = self.process_next()
            except Exception:
                # A claim/result persistence error needs operator recovery, not replay.
                # Fail readiness and new submissions without logging raw state/errors.
                self.coordinator_failure = "coordinator_failed"
                return
            if not processed:
                self.wake.wait(0.25)
                self.wake.clear()
