"""Tenant-bound application service and a durable, single-worker local queue."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

from proofbeak.engine import recover_cleanup, run_trial
from proofbeak.models import canonical, fingerprint
from proofbeak.observer import OBSERVER_VERSION
from proofbeak.packs import builtins
from proofbeak.store import Store


class ServiceError(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Tenant:
    id: str
    token_sha256: str
    providers: dict


class TrialService:
    def __init__(self, root, tenants, pending_limit=8):
        self.root = Path(root).resolve()
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
        self.database = self.root / "queue.sqlite3"
        self.lock = None
        self.worker = None
        self.stop = Event()
        self.wake = Event()
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, client_key TEXT NOT NULL,
                request_hash TEXT NOT NULL, request TEXT NOT NULL, context TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, reason TEXT, recovery TEXT,
                created_at TEXT NOT NULL, UNIQUE(tenant, client_key))""")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
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
        with self._db() as db:
            db.execute("UPDATE jobs SET state='interrupted', reason='recovery_required' "
                       "WHERE state='running'")
        self.stop.clear()
        if background:
            self.worker = Thread(target=self._loop, name="proofbeak-worker", daemon=False)
            self.worker.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.worker:
            self.worker.join(timeout=30)
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
        return match

    def _adapters(self, tenant_id, candidates):
        tenant = self.tenants.get(tenant_id)
        if tenant is None:
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
        return {"pack_id": pack.id, "pack_version": pack.version,
                "observer_version": OBSERVER_VERSION,
                "providers": [{"name": a.name, "version": a.version, "profile": a.profile,
                               "evidence_level": a.evidence_level} for a in adapters]}

    def submit(self, tenant, spec, candidates, key):
        if self.lock is None:
            raise ServiceError("service_unavailable", 503)
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ServiceError("invalid_idempotency_key", 422)
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
            row = db.execute("SELECT * FROM jobs WHERE tenant=? AND client_key=?", (tenant, key)).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise ServiceError("idempotency_conflict", 409)
                return {"run_id": row["id"], "state": row["state"]}
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE tenant=? "
                               "AND state IN ('queued','running')", (tenant,)).fetchone()[0]
            if count >= self.pending_limit:
                raise ServiceError("tenant_queue_full", 429)
            run_id = uuid4().hex
            db.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, NULL, ?)",
                       (run_id, tenant, key, request_hash, request, canonical(context),
                        datetime.now(UTC).isoformat()))
        self.wake.set()
        return {"run_id": run_id, "state": "queued"}

    def _row(self, tenant, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ServiceError("not_found", 404)
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE tenant=? AND id=?", (tenant, run_id)).fetchone()
        if row is None:
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
                "cleanup_recovery": json.loads(row["recovery"]) if row["recovery"] else None}

    def comparison(self, tenant, run_id):
        row = self._row(tenant, run_id)
        if row["result"] is None:
            raise ServiceError("result_not_available", 409)
        report = json.loads(row["result"])
        for index, item in enumerate(report["candidates"]):
            item.pop("evidence", None)
            item["evidence_ref"] = f"{run_id}:{index}"
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
        return {"evidence_id": evidence_id, "run_id": run_id, **candidates[int(index)]}

    def cleanup(self, tenant, run_id):
        row = self._row(tenant, run_id)
        if row["state"] in {"queued", "running"}:
            raise ServiceError("run_still_active", 409)
        ledger = self.root / "ledgers" / f"{run_id}.sqlite3"
        outcomes = []
        if ledger.exists():
            request = json.loads(row["request"])
            adapters = self._adapters(tenant, request["candidates"])
            try:
                store = Store(ledger)
            except ValueError:
                raise ServiceError("run_still_active", 409) from None
            try:
                outcomes = recover_cleanup(store, adapters)
            finally:
                store.close()
        with self._db() as db:
            db.execute("UPDATE jobs SET recovery=? WHERE id=? AND tenant=?",
                       (canonical(outcomes), run_id, tenant))
        return {"run_id": run_id, "cleanup_recovery": outcomes}

    def packs(self, tenant):
        if tenant not in self.tenants:
            raise ServiceError("unauthorized", 401)
        return {"packages": builtins().describe(), "providers": sorted(self.tenants[tenant].providers)}

    def process_next(self):
        if self.lock is None:
            raise RuntimeError("start the worker before claiming jobs")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at, id LIMIT 1").fetchone()
            if row is None:
                return False
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (row["id"],))
        report, reason = None, None
        try:
            request = json.loads(row["request"])
            context = self._context(row["tenant"], request["test"], request["candidates"])
            if fingerprint(context) != fingerprint(json.loads(row["context"])):
                raise ServiceError("execution_context_changed", 409)
            adapters = self._adapters(row["tenant"], request["candidates"])
            store = Store(self.root / "ledgers" / f'{row["id"]}.sqlite3')
            try:
                report = run_trial(request["test"], adapters, store, row["id"])
            finally:
                store.close()
            report["execution_run_id"] = report["run_id"]
            report["run_id"] = row["id"]
        except ServiceError as exc:
            reason = exc.code
        except Exception:
            # Provider exceptions may contain secrets. Only a stable code is persisted or returned.
            reason = "execution_interrupted"
        with self._db() as db:
            db.execute("UPDATE jobs SET state=?, result=?, reason=? WHERE id=?", (
                "completed" if report else "interrupted", canonical(report) if report else None,
                reason, row["id"]))
        return True

    def _loop(self):
        while not self.stop.is_set():
            if not self.process_next():
                self.wake.wait(0.25)
                self.wake.clear()
