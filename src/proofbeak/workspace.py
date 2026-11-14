"""Private workspace helpers. Reads never schedule work or refresh historical evidence."""

import json
import math
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from proofbeak.models import canonical, fingerprint


def error(code, status=422):
    from proofbeak.service import ServiceError
    raise ServiceError(code, status)


def candidate_outcome(alias="j"):
    # This is recorded acceptance, deliberately distinct from the current evidence/CI gate.
    document = f"CASE WHEN json_valid({alias}.result) THEN {alias}.result ELSE '{{}}' END"
    candidates = f"json_each({document},'$.candidates')"
    return f"""CASE WHEN {alias}.state IN ('queued','running') THEN 'running'
        WHEN {alias}.state!='completed' OR NOT EXISTS(SELECT 1 FROM {candidates}) THEN 'inconclusive'
        WHEN EXISTS(SELECT 1 FROM {candidates} c WHERE coalesce(json_extract(c.value,'$.verdict'),'')
            NOT IN ('pass','fail')) THEN 'inconclusive'
        WHEN EXISTS(SELECT 1 FROM {candidates} c WHERE json_extract(c.value,'$.verdict')='fail') THEN 'fail'
        ELSE 'pass' END"""


class WorkspaceService:
    def _workspace_tenant(self, tenant):
        if tenant not in self.tenants or self._disabled(self.tenants[tenant].token_sha256):
            error("unauthorized", 401)

    def _init_workspace(self):
        with self._db() as db:
            db.executescript("""
                CREATE INDEX IF NOT EXISTS suite_history ON acceptance_suites(tenant,created_at,id);
                CREATE INDEX IF NOT EXISTS job_completion ON jobs(tenant,completed_at,id);
                CREATE TABLE IF NOT EXISTS workspace_items (
                    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
                    document TEXT, source_id TEXT, created_at TEXT NOT NULL, UNIQUE(tenant,kind,name));
                CREATE INDEX IF NOT EXISTS workspace_owner ON workspace_items(tenant,kind,created_at,id);
            """)

    def search_history(self, tenant, kind="suite", limit=25, before=None, q="", state=None,
                       outcome=None, provider=None, since=None, until=None):
        self._workspace_tenant(tenant)
        states = {"queued", "running", "completed", "interrupted", "cancelled"}
        if kind not in {"suite", "trial"} or type(limit) is not int or not 1 <= limit <= 100:
            error("invalid_history_filter")
        if not isinstance(q, str) or len(q) > 120 or state not in states | {None} \
                or outcome not in {None, "pass", "fail", "inconclusive", "running"} \
                or (provider is not None and (not isinstance(provider, str) or len(provider) > 64)):
            error("invalid_history_filter")
        dates = {}
        for name, value in (("since", since), ("until", until)):
            if value is not None:
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError
                    dates[name] = datetime.fromisoformat(value).replace(tzinfo=UTC)
                    if name == "until":
                        dates[name] + timedelta(days=1)
                except (ValueError, TypeError, OverflowError):
                    error("invalid_history_filter")
        if "since" in dates and "until" in dates and dates["since"] > dates["until"]:
            error("invalid_history_filter")
        suite = kind == "suite"
        table = "acceptance_suites" if suite else "jobs"
        name = "json_extract(s.definition,'$.name')" if suite else "json_extract(s.request,'$.test.name')"
        recorded = candidate_outcome("s")
        if suite:
            child = candidate_outcome("j")
            members = "suite_members m LEFT JOIN jobs j ON j.id=m.job_id AND j.tenant=s.tenant"
            recorded = f"""CASE WHEN s.state IN ('queued','running') THEN 'running'
                WHEN s.state!='completed' OR NOT EXISTS(SELECT 1 FROM suite_members WHERE suite_id=s.id)
                    OR EXISTS(SELECT 1 FROM {members} WHERE m.suite_id=s.id
                        AND (j.id IS NULL OR ({child}) NOT IN ('pass','fail'))) THEN 'inconclusive'
                WHEN EXISTS(SELECT 1 FROM {members} WHERE m.suite_id=s.id AND ({child})='fail') THEN 'fail'
                ELSE 'pass' END"""
        clauses, values = ["s.tenant=?"], [tenant]
        if before:
            cursor = self._suite_row(tenant, before) if suite else self._row(tenant, before)
            clauses.append("(s.created_at<? OR (s.created_at=? AND s.id<?))")
            values.extend([cursor["created_at"], cursor["created_at"], before])
        if q:
            clauses.append(f"(instr(casefold(coalesce({name},'')),?)>0 OR instr(s.id,?)>0)")
            values.extend([q.casefold(), q.casefold()])
        if state:
            clauses.append("s.state=?")
            values.append(state)
        if outcome:
            clauses.append(f"({recorded})=?")
            values.append(outcome)
        if provider:
            document = "s.definition" if suite else "s.request"
            selector = ("json_each(s.definition,'$.cases') cases, json_each(cases.value,'$.candidates') p"
                        if suite else f"json_each({document},'$.candidates') p")
            clauses.append(f"EXISTS(SELECT 1 FROM {selector} WHERE p.value=?)")
            values.append(provider)
        if "since" in dates:
            clauses.append("s.created_at>=?")
            values.append(dates["since"].isoformat())
        if "until" in dates:
            clauses.append("s.created_at<?")
            values.append((dates["until"] + timedelta(days=1)).isoformat())
        with self._db() as db:
            rows = db.execute(f"SELECT s.id,{name} AS name,s.state,s.reason,s.created_at,s.completed_at,"
                              f"({recorded}) AS outcome FROM {table} s WHERE {' AND '.join(clauses)} "
                              "ORDER BY s.created_at DESC,s.id DESC LIMIT ?", [*values, limit + 1]).fetchall()
        items = [{"suite_id" if suite else "run_id": row["id"], "name": row["name"] or "Trial",
                  "state": row["state"], "recorded_outcome": row["outcome"], "reason_code": row["reason"],
                  "created_at": row["created_at"], "completed_at": row["completed_at"]} for row in rows[:limit]]
        return {"suites" if suite else "runs": items,
                "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
                "outcome_scope": "recorded_acceptance_not_current_evidence_gate"}

    def workspace_progress(self, tenant, identifier, kind="suite"):
        self._workspace_tenant(tenant)
        if kind not in {"suite", "trial"}:
            error("invalid_history_filter")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            error("not_found", 404)
        table = "acceptance_suites" if kind == "suite" else "jobs"
        with self._db() as db:
            row = db.execute(f"SELECT state,reason,completed_at FROM {table} WHERE tenant=? AND id=?",
                             (tenant, identifier)).fetchone()
            if row is None:
                # Retain ordinary deleted/foreign record semantics without reading report payloads.
                self._suite_row(tenant, identifier) if kind == "suite" else self._row(tenant, identifier)
            members = db.execute("SELECT m.ordinal,j.state,j.reason,j.completed_at FROM suite_members m "
                "LEFT JOIN jobs j ON j.id=m.job_id AND j.tenant=? WHERE m.suite_id=? ORDER BY m.ordinal",
                (tenant, identifier)).fetchall() if kind == "suite" else [row]
        data = {"id": identifier, "state": row["state"], "reason_code": row["reason"],
                "members": [{"state": m["state"] or "not_run", "reason_code": m["reason"],
                             "completed_at": m["completed_at"]} for m in members]}
        data["completed"] = sum(m["state"] == "completed" for m in members)
        data["total"] = len(members)
        data["revision"] = fingerprint(data)
        return data

    def save_workspace_item(self, tenant, kind, name, definition=None, source_id=None):
        self._workspace_tenant(tenant)
        if kind not in {"template", "baseline"} or not isinstance(name, str) \
                or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
            error("invalid_workspace_item")
        if kind == "template":
            if not self.validate_suite(tenant, definition)["valid"]:
                error("invalid_suite")
            document = canonical(definition)
            source_id = None
        else:
            if self.get_suite(tenant, source_id)["gate"] != "pass":
                error("baseline_requires_passing_evidence", 409)
            document = None
        identifier, now = uuid4().hex, datetime.now(UTC).isoformat()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM workspace_items WHERE tenant=? AND kind=? AND name=?",
                          (tenant, kind, name.strip())).fetchone():
                error("workspace_name_exists", 409)
            if db.execute("SELECT count(*) FROM workspace_items WHERE tenant=? AND kind=?", (tenant, kind)).fetchone()[0] >= 100:
                error("workspace_item_limit", 409)
            db.execute("INSERT INTO workspace_items VALUES (?,?,?,?,?,?,?)",
                       (identifier, tenant, kind, name.strip(), document, source_id, now))
            self._audit(db, tenant, "workspace_item_saved", source_id, {"id": identifier, "kind": kind})
        return {"id": identifier, "kind": kind, "name": name.strip(), "source_id": source_id, "created_at": now}

    def list_workspace_items(self, tenant, kind):
        self._workspace_tenant(tenant)
        if kind not in {"template", "baseline"}:
            error("invalid_workspace_item")
        with self._db() as db:
            rows = db.execute("SELECT w.id,w.kind,w.name,w.source_id,w.created_at,s.state AS source_state "
                "FROM workspace_items w LEFT JOIN acceptance_suites s ON s.id=w.source_id AND s.tenant=w.tenant "
                "WHERE w.tenant=? AND w.kind=? ORDER BY w.created_at DESC,w.id DESC LIMIT 100", (tenant, kind)).fetchall()
        return {"items": [dict(row) for row in rows]}

    def workspace_item(self, tenant, identifier, delete=False):
        self._workspace_tenant(tenant)
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            error("not_found", 404)
        with self._db() as db:
            row = db.execute("SELECT * FROM workspace_items WHERE tenant=? AND id=?", (tenant, identifier)).fetchone()
            if row is None:
                error("not_found", 404)
            if delete:
                db.execute("DELETE FROM workspace_items WHERE tenant=? AND id=?", (tenant, identifier))
                self._audit(db, tenant, "workspace_item_deleted", detail={"id": identifier, "kind": row["kind"]})
                return {"deleted": True}
        return {"id": row["id"], "kind": row["kind"], "name": row["name"], "source_id": row["source_id"],
                "definition": json.loads(row["document"]) if row["document"] else None}

    def workspace_metrics(self, tenant):
        self._workspace_tenant(tenant)
        now = datetime.now(UTC)
        with self._db() as db:
            counts = {r["state"]: r["n"] for r in db.execute("SELECT state,count(*) n FROM jobs WHERE tenant=? GROUP BY state", (tenant,))}
            oldest = db.execute("SELECT min(created_at) FROM jobs WHERE tenant=? AND state='queued'", (tenant,)).fetchone()[0]
            pending_suites = db.execute("SELECT count(*),min(created_at) FROM acceptance_suites WHERE tenant=? AND state='queued'", (tenant,)).fetchone()
            sizes = db.execute("SELECT coalesce(sum(length(CAST(result AS BLOB))),0) FROM jobs WHERE tenant=?", (tenant,)).fetchone()[0]
            # Operational counters are aggregate metadata; no fixture, endpoint, key or payload is returned.
            cleanup = db.execute("SELECT count(DISTINCT j.id) FROM jobs j,json_each(CASE WHEN json_valid(j.result) "
                "THEN j.result ELSE '{}' END,'$.candidates') c WHERE j.tenant=? AND "
                "coalesce(json_extract(c.value,'$.cleanup_status'),'')!='cleaned' AND NOT ("
                "json_valid(coalesce(j.recovery,'[]')) AND json_array_length(coalesce(j.recovery,'[]'))>0 AND NOT EXISTS("
                "SELECT 1 FROM json_each(coalesce(j.recovery,'[]')) r WHERE coalesce(json_extract(r.value,'$.status'),'')!='cleaned'))",
                (tenant,)).fetchone()[0]
            rows = db.execute("SELECT state,reason,created_at,completed_at FROM jobs WHERE tenant=? AND completed_at IS NOT NULL "
                              "ORDER BY completed_at DESC LIMIT 100", (tenant,)).fetchall()
        oldest = min(filter(None, (oldest, pending_suites[1])), default=None)
        durations = sorted(max(0, (datetime.fromisoformat(r["completed_at"]) - datetime.fromisoformat(r["created_at"])).total_seconds()) for r in rows)
        return {"scope": "authenticated_tenant", "generated_at": now.isoformat(), "jobs": counts,
                "queued_suites": pending_suites[0], "oldest_queue_seconds": max(0, (now - datetime.fromisoformat(oldest)).total_seconds()) if oldest else 0,
                "cleanup_pending_runs": cleanup, "retained_report_bytes": sizes, "sample_size": len(rows),
                "sample_limit": 100, "recent_timeout_runs": sum(any(code in (r["reason"] or "") for code in ("timeout", "timed_out", "deadline")) for r in rows),
                "p95_queue_and_run_seconds": durations[max(0, math.ceil(len(durations) * .95) - 1)] if durations else None,
                "maintenance": "deferred" if self.maintenance_deferred else "ok"}
