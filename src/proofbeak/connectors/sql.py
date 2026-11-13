"""Named SQL statements in a run-owned SQLite database or PostgreSQL schema."""

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from proofbeak.adapters.base import ProviderError
from proofbeak.connectors import Connector
from proofbeak.models import canonical


def record_operations():
    result = {}
    for action, names, read_only in (("put", ["key", "value"], False), ("get", ["key"], True),
            ("delete", ["key"], False), ("list", [], True), ("rollback", ["key", "value"], False)):
        properties = {name: {"type": "string", "maxLength": 4096, "minLength": 1} for name in names}
        result["record." + action] = {"input_schema": {"type": "object", "properties": properties,
            "required": names, "additionalProperties": False}, "output_schema": {"type": "object"},
            "read_only": read_only, "binding": {"action": action}}
    return result


class SQLConnector(Connector):
    def __init__(self, name, settings, secrets=None):
        settings = {**settings, "operations": settings.get("operations", record_operations())}
        super().__init__(name, settings, secrets)
        if set(settings) - {"kind", "connection", "operations", "provider_version"}:
            raise ValueError("unknown SQL connector field")
        self.sqlite = settings["kind"] == "sqlite"
        connection = settings["connection"]
        if set(connection) - ({"root"} if self.sqlite else {"secret_env"}):
            raise ValueError("unknown SQL connection field")
        if self.sqlite:
            self.root = Path(connection["root"]).absolute()
        elif connection.get("secret_env", {}).keys() != {"dsn"}:
            raise ValueError("PostgreSQL requires a dsn secret environment variable")
        for definition in self.catalog.operations.values():
            binding = definition["binding"]
            if binding.get("action") in {"put", "get", "delete", "list", "rollback"}:
                if set(binding) != {"action"} or definition["read_only"] != (binding["action"] in {"get", "list"}):
                    raise ValueError("invalid SQL action declaration")
            elif set(binding) != {"statement"} or not isinstance(binding["statement"], str) or \
                    "{table}" not in binding["statement"] or ";" in binding["statement"] or len(binding["statement"]) > 4096:
                raise ValueError("named SQL statements require the isolated {table} and one statement")

    def _file(self, resource):
        scope = self.scope(resource)
        path = self.root / (scope + ".sqlite3")
        if self.root.is_symlink() or path.is_symlink():
            raise ProviderError("resource_scope_violation")
        return path

    @contextmanager
    def _db(self, resource, *, create=False):
        scope = self.scope(resource)
        db = None
        try:
            if self.sqlite:
                path = self._file(resource)
                if not create and not path.is_file():
                    raise ProviderError("resource_missing")
                db = sqlite3.connect(str(path), timeout=3)
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA busy_timeout=3000")
            else:
                try:
                    import psycopg
                    from psycopg.rows import dict_row
                except ImportError:
                    raise ProviderError("postgres_extra_required") from None
                db = psycopg.connect(self.secrets.get("dsn", ""), connect_timeout=3, autocommit=True, row_factory=dict_row)
                db.execute("SET statement_timeout = '5000ms'")
                db.execute('SET search_path TO "pb_' + scope + '", pg_catalog')
            yield db
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("database_operation_failed") from None
        finally:
            if db:
                db.close()

    def provision(self, resource, trial):
        scope = self.scope(resource)
        if self.sqlite:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.root.is_symlink() or self.root.stat().st_mode & 0o077:
                raise ProviderError("resource_scope_violation")
            try:
                fd = os.open(self._file(resource), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                raise ProviderError("resource_conflict") from None
        with self._db(resource, create=True) as db:
            if not self.sqlite:
                exists = db.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", ("pb_" + scope,)).fetchone()
                if exists:
                    raise ProviderError("resource_conflict")
                db.execute('CREATE SCHEMA "pb_' + scope + '"')
            db.execute("CREATE TABLE owner (scope TEXT NOT NULL)")
            db.execute("INSERT INTO owner VALUES (?)" if self.sqlite else "INSERT INTO owner VALUES (%s)", (scope,))
            db.execute("CREATE TABLE records (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            if self.sqlite:
                db.commit()

    def _owned(self, db, resource):
        row = db.execute("SELECT scope FROM owner").fetchone()
        if not row or row["scope"] != self.scope(resource):
            raise ProviderError("ownership_unverified")

    def cleanup(self, resource):
        scope = self.scope(resource)
        if self.sqlite and not self._file(resource).exists():
            return
        with self._db(resource) as db:
            if not self.sqlite and not db.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", ("pb_" + scope,)).fetchone():
                return
            self._owned(db, resource)
            if not self.sqlite:
                db.execute('DROP SCHEMA "pb_' + scope + '" CASCADE')
                if db.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", ("pb_" + scope,)).fetchone():
                    raise ProviderError("cleanup_unconfirmed")
        if self.sqlite:
            self._file(resource).unlink()

    def invoke(self, resource, operation, arguments):
        definition = self.catalog.validate(operation, arguments)
        binding, readonly = definition["binding"], definition["read_only"]
        with self._db(resource) as db:
            self._owned(db, resource)
            if self.sqlite:
                db.execute("PRAGMA query_only=" + ("ON" if readonly else "OFF"))
            else:
                db.execute("BEGIN READ ONLY" if readonly else "BEGIN")
            try:
                action = binding.get("action")
                marker = "?" if self.sqlite else "%s"
                if action in {"put", "rollback"}:
                    db.execute(f"INSERT INTO records(key,value) VALUES ({marker},{marker}) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (arguments["key"], arguments["value"]))
                    result = {"accepted": True, "rolled_back": action == "rollback"}
                elif action == "delete":
                    db.execute("DELETE FROM records WHERE key=" + marker, (arguments["key"],))
                    result = {"accepted": True}
                elif action == "get":
                    row = db.execute("SELECT key,value FROM records WHERE key=" + marker, (arguments["key"],)).fetchone()
                    result = {"exists": bool(row), **(dict(row) if row else {})}
                elif action == "list":
                    rows = db.execute("SELECT key,value FROM records ORDER BY key LIMIT 257").fetchall()
                    if len(rows) > 256:
                        raise ProviderError("observation_budget_exceeded")
                    result = {"rows": [dict(row) for row in rows], "count": len(rows)}
                else:
                    statement = binding["statement"].replace("{table}", "records")
                    if not self.sqlite:
                        statement = re.sub(r":([A-Za-z][A-Za-z0-9_]*)", r"%(\1)s", statement)
                    cursor = db.execute(statement, arguments)
                    rows = cursor.fetchmany(257) if cursor.description else []
                    if len(rows) > 256:
                        raise ProviderError("observation_budget_exceeded")
                    result = {"rows": [dict(row) for row in rows], "count": len(rows)}
                canonical(result)
                if action == "rollback":
                    db.rollback()
                else:
                    db.commit()
                return result
            except BaseException:
                db.rollback()
                raise
