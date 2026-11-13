"""Tenant-owned durable suite plans. Only one member per suite can be active."""

import json
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from proofbeak.models import canonical, fingerprint
from proofbeak.suites import REPORT_FORMAT, expand_suite, summarize
from proofbeak.validation import issue_from, validation_result


def service_error(code, status=422):
    from proofbeak.service import ServiceError
    raise ServiceError(code, status)


class SuiteService:
    def _init_suites(self):
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS acceptance_suites (
                    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, client_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL, suite_hash TEXT NOT NULL, definition TEXT NOT NULL,
                    state TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL, completed_at TEXT,
                    raw_purged_at TEXT, UNIQUE(tenant,client_key));
                CREATE TABLE IF NOT EXISTS suite_members (
                    suite_id TEXT NOT NULL, ordinal INTEGER NOT NULL, entry_id TEXT NOT NULL,
                    case_id TEXT NOT NULL, parameters TEXT NOT NULL, test_hash TEXT NOT NULL,
                    request TEXT, context TEXT, job_id TEXT NOT NULL, scheduled INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(suite_id,ordinal), UNIQUE(job_id));
                CREATE TABLE IF NOT EXISTS suite_tombstones (
                    tenant TEXT NOT NULL, id TEXT NOT NULL, key_hash TEXT NOT NULL,
                    deleted_at TEXT NOT NULL, PRIMARY KEY(tenant,id), UNIQUE(tenant,key_hash));
                CREATE INDEX IF NOT EXISTS suite_dispatch ON acceptance_suites(state,created_at,id);
            """)

    def _pending_slots(self, db, tenant):
        standalone = db.execute("SELECT COUNT(*) FROM jobs j WHERE tenant=? AND state IN ('queued','running') "
                                "AND NOT EXISTS (SELECT 1 FROM suite_members m WHERE m.job_id=j.id)", (tenant,)).fetchone()[0]
        suites = db.execute("SELECT COUNT(*) FROM acceptance_suites WHERE tenant=? AND state IN ('queued','running')",
                            (tenant,)).fetchone()[0]
        return standalone + suites

    def validate_suite(self, tenant, definition):
        # Establish tenant identity before returning any schema diagnostics.
        if tenant not in self.tenants or self._disabled(self.tenants[tenant].token_sha256):
            service_error("unauthorized", 401)
        try:
            plan = expand_suite(definition)
        except (ValueError, TypeError, RecursionError) as error:
            return validation_result([issue_from(error)])
        issues = []
        for index, item in enumerate(plan):
            result = self.validate_trial(tenant, item["test"], item["candidates"])
            for issue in result["issues"]:
                issues.append({**issue, "path": ["executions", index, *issue.get("path", [])], "entry": item["id"]})
        return {**validation_result(issues), "executions": len(plan), "suite_hash": fingerprint(definition),
                "provider_work_performed": False}

    def submit_suite(self, tenant, definition, key):
        if not self.available:
            service_error("service_unavailable", 503)
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            service_error("invalid_idempotency_key")
        try:
            plan = expand_suite(definition)
        except (ValueError, TypeError, RecursionError):
            service_error("invalid_suite")
        contexts = [self._context(tenant, item["test"], item["candidates"]) for item in plan]
        request_hash = fingerprint({"definition": definition, "contexts": contexts})
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM suite_tombstones WHERE tenant=? AND key_hash=?", (tenant, fingerprint(key))).fetchone():
                service_error("suite_deleted", 410)
            old = db.execute("SELECT id,state,request_hash FROM acceptance_suites WHERE tenant=? AND client_key=?", (tenant, key)).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    service_error("idempotency_conflict", 409)
                return {"suite_id": old["id"], "state": old["state"]}
            prefix = self.key_prefix(db)
            if prefix and not key.startswith(prefix):
                service_error("restore_key_prefix_required", 409)
            if self._pending_slots(db, tenant) >= self.pending_limit:
                service_error("tenant_queue_full", 429)
            suite_id = uuid4().hex
            now = datetime.now(UTC).isoformat()
            db.execute("INSERT INTO acceptance_suites (id,tenant,client_key,request_hash,suite_hash,definition,state,created_at) "
                       "VALUES (?,?,?,?,?,?,'queued',?)", (suite_id, tenant, key, request_hash,
                                                          fingerprint(definition), canonical(definition), now))
            for index, (item, context) in enumerate(zip(plan, contexts)):
                request = {"test": item["test"], "candidates": item["candidates"]}
                db.execute("INSERT INTO suite_members VALUES (?,?,?,?,?,?,?,?,?,0)", (
                    suite_id, index, item["id"], item["case_id"], canonical(item["parameters"]),
                    item["test_hash"], canonical(request), canonical(context), uuid4().hex))
            self._audit(db, tenant, "suite_submitted", suite_id, {"executions": len(plan), "suite_hash": fingerprint(definition)})
        self.wake.set()
        return {"suite_id": suite_id, "state": "queued"}

    def _schedule_suites(self, db):
        # Called under the same dispatch transaction as a trial claim. Plans are not worker processes.
        parents = db.execute("SELECT * FROM acceptance_suites WHERE state IN ('queued','running') "
                             "ORDER BY created_at,id LIMIT 100").fetchall()
        for parent in parents:
            members = db.execute("SELECT m.*,j.state AS job_state,j.result,j.reason FROM suite_members m "
                                 "LEFT JOIN jobs j ON j.id=m.job_id WHERE m.suite_id=? ORDER BY m.ordinal", (parent["id"],)).fetchall()
            if any(m["job_state"] in {"queued", "running"} for m in members):
                continue
            reason = None
            for member in members:
                if member["scheduled"]:
                    if member["job_state"] != "completed":
                        reason = member["reason"] or "suite_member_unavailable"
                    elif member["result"] and any(c["cleanup_status"] != "cleaned" for c in json.loads(member["result"])["candidates"]):
                        reason = "cleanup_required"
            remaining = next((m for m in members if not m["scheduled"]), None)
            if reason or remaining is None:
                db.execute("UPDATE acceptance_suites SET state=?,reason=?,completed_at=? WHERE id=?", (
                    "interrupted" if reason else "completed", reason, datetime.now(UTC).isoformat(), parent["id"]))
                db.execute("UPDATE suite_members SET request=NULL,context=NULL WHERE suite_id=?", (parent["id"],))
                self._audit(db, parent["tenant"], "suite_completed", parent["id"], {"reason_code": reason})
                continue
            key = self.key_prefix(db) + "suite:" + parent["id"] + ":" + str(remaining["ordinal"])
            request_hash = fingerprint({"request": json.loads(remaining["request"]), "context": json.loads(remaining["context"])})
            db.execute("INSERT INTO jobs (id,tenant,client_key,request_hash,request,context,state,created_at) "
                       "VALUES (?,?,?,?,?,?,'queued',?)", (remaining["job_id"], parent["tenant"], key, request_hash,
                       remaining["request"], remaining["context"], datetime.now(UTC).isoformat()))
            db.execute("UPDATE suite_members SET scheduled=1,request=NULL,context=NULL WHERE suite_id=? AND ordinal=?",
                       (parent["id"], remaining["ordinal"]))
            db.execute("UPDATE acceptance_suites SET state='running' WHERE id=?", (parent["id"],))
            self._audit(db, parent["tenant"], "suite_member_submitted", remaining["job_id"], {"suite_id": parent["id"]})

    def _suite_row(self, tenant, suite_id):
        if not isinstance(suite_id, str) or not re.fullmatch(r"[a-f0-9]{32}", suite_id):
            service_error("not_found", 404)
        with self._db() as db:
            row = db.execute("SELECT * FROM acceptance_suites WHERE tenant=? AND id=?", (tenant, suite_id)).fetchone()
            if row is None:
                gone = db.execute("SELECT 1 FROM suite_tombstones WHERE tenant=? AND id=?", (tenant, suite_id)).fetchone()
                service_error("suite_deleted" if gone else "not_found", 410 if gone else 404)
        return row

    def get_suite(self, tenant, suite_id):
        parent = self._suite_row(tenant, suite_id)
        with self._db() as db:
            members = db.execute("SELECT m.*,j.state AS job_state,j.result,j.reason FROM suite_members m "
                                 "LEFT JOIN jobs j ON j.id=m.job_id WHERE m.suite_id=? ORDER BY ordinal", (suite_id,)).fetchall()
        entries = []
        for member in members:
            report = None
            if member["result"]:
                try:
                    report = self.comparison(tenant, member["job_id"])
                except Exception:
                    # Retention may remove a child between the list and read. Never fabricate success.
                    report = None
            entries.append({"id": member["entry_id"], "case_id": member["case_id"],
                "parameters": json.loads(member["parameters"]), "test_hash": member["test_hash"],
                "run_id": member["job_id"] if member["scheduled"] else None,
                "state": member["job_state"] or "not_run", "report": report,
                "reason_code": member["reason"] or (parent["reason"] if parent["state"] not in {"queued", "running"} else None)})
        definition = json.loads(parent["definition"])
        return {"schema_version": REPORT_FORMAT, "suite_id": suite_id, "name": definition.get("name", "Acceptance suite"),
            "suite_hash": parent["suite_hash"], "state": parent["state"], "reason_code": parent["reason"],
            "created_at": parent["created_at"], "completed_at": parent["completed_at"],
            "raw_purged_at": parent["raw_purged_at"], "definition": definition, "entries": entries,
            **summarize(entries, parent["state"])}

    def list_suites(self, tenant, limit=25, before=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            service_error("invalid_limit")
        clause, values = "tenant=?", [tenant]
        if before:
            cursor = self._suite_row(tenant, before)
            clause += " AND (created_at < ? OR (created_at=? AND id<?))"
            values += [cursor["created_at"], cursor["created_at"], before]
        with self._db() as db:
            rows = db.execute("SELECT id,definition,state,reason,created_at,completed_at,suite_hash FROM acceptance_suites "
                              f"WHERE {clause} ORDER BY created_at DESC,id DESC LIMIT ?", [*values, limit + 1]).fetchall()
        return {"suites": [{"suite_id": r["id"], "name": json.loads(r["definition"]).get("name", "Acceptance suite"),
                "state": r["state"], "reason_code": r["reason"], "created_at": r["created_at"],
                "completed_at": r["completed_at"], "suite_hash": r["suite_hash"]} for r in rows[:limit]],
                "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None}

    def cancel_suite(self, tenant, suite_id):
        self._suite_row(tenant, suite_id)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT state FROM acceptance_suites WHERE tenant=? AND id=?", (tenant, suite_id)).fetchone()[0]
            if state in {"queued", "running"}:
                db.execute("UPDATE acceptance_suites SET state='cancelled',reason='suite_cancelled',completed_at=? "
                           "WHERE tenant=? AND id=?", (datetime.now(UTC).isoformat(), tenant, suite_id))
                db.execute("UPDATE suite_members SET request=NULL,context=NULL WHERE suite_id=?", (suite_id,))
                active = db.execute("SELECT j.id FROM jobs j JOIN suite_members m ON m.job_id=j.id "
                                    "WHERE m.suite_id=? AND j.state IN ('queued','running')", (suite_id,)).fetchall()
                self._audit(db, tenant, "suite_cancelled", suite_id)
            else:
                active = []
        for row in active:
            self.cancel(tenant, row["id"])
        return self.get_suite(tenant, suite_id)

    def maintain_suites(self, now=None):
        now = now or datetime.now(UTC)
        changed = False
        with self._db() as db:
            rows = db.execute("SELECT * FROM acceptance_suites WHERE state NOT IN ('queued','running') AND completed_at<? "
                "AND (raw_purged_at IS NULL OR completed_at<?) ORDER BY completed_at LIMIT 100",
                ((now - timedelta(days=7)).isoformat(), (now - timedelta(days=30)).isoformat())).fetchall()
            for row in rows:
                age = now - datetime.fromisoformat(row["completed_at"])
                if age >= timedelta(days=7) and not row["raw_purged_at"]:
                    name = json.loads(row["definition"]).get("name", "Acceptance suite")
                    db.execute("UPDATE acceptance_suites SET definition=?,raw_purged_at=? WHERE id=?", (
                        canonical({"id": "suite.v1", "name": name, "redacted": True}), now.isoformat(), row["id"]))
                    db.execute("UPDATE suite_members SET parameters='{}',request=NULL,context=NULL WHERE suite_id=?", (row["id"],))
                    changed = True
                if age >= timedelta(days=30):
                    children = db.execute("SELECT j.state,j.result FROM jobs j JOIN suite_members m ON j.id=m.job_id "
                                          "WHERE m.suite_id=?", (row["id"],)).fetchall()
                    if any(c["state"] in {"queued", "running"} or c["result"] and any(
                            item["cleanup_status"] != "cleaned" for item in json.loads(c["result"])["candidates"]) for c in children):
                        continue
                    db.execute("INSERT OR IGNORE INTO suite_tombstones VALUES (?,?,?,?)", (
                        row["tenant"], row["id"], fingerprint(row["client_key"]), now.isoformat()))
                    db.execute("DELETE FROM suite_members WHERE suite_id=?", (row["id"],))
                    db.execute("DELETE FROM acceptance_suites WHERE id=?", (row["id"],))
                    changed = True
        return changed
