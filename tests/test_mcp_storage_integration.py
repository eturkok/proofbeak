"""Actual MCP -> REST -> subprocess -> two HTTP services, with independent mutation journals."""

import asyncio
import hashlib
import importlib.util
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import unittest
from collections import Counter
from pathlib import Path

from proofbeak.interfaces.mcp import BridgeClient
from proofbeak.recovery import backup, restore
from proofbeak.service import ServiceError
from webdav_fixtures import AVAILABLE, PASSWORD, WebDAVFixtures

AGENTS = all(importlib.util.find_spec(name) for name in ("mcp", "fastapi", "uvicorn", "httpx"))
ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ["webdav:wsgidav", "webdav:apache"]


@unittest.skipUnless(AVAILABLE and AGENTS, "install .[agents], build both WebDAV fixtures and run Docker")
class McpStorageIntegrationTests(WebDAVFixtures, unittest.TestCase):
    fixture_report_environment = "PROOFBEAK_AGENT_FLOW_REPORT_DIR"

    def wait_mutations(self, count):
        deadline = time.monotonic() + 5
        while True:
            rows = self.mutations()
            if all(len(items) >= count for items in rows.values()):
                self.assertEqual({name: len(items) for name, items in rows.items()},
                                 {"wsgidav": count, "apache": count})
                self.assertTrue(all(200 <= r["status"] < 300 for items in rows.values() for r in items))
                return rows
            if time.monotonic() >= deadline:
                self.fail("provider mutation journals did not reach their expected count")
            time.sleep(.05)

    def client(self, env, output, key="mcp-storage", *, candidates=CANDIDATES, extra=(), code=0):
        command = [sys.executable, str(ROOT / "examples/mcp_compare.py"), "--key", key,
                   "--output", str(output), "--html", str(output.with_suffix(".html"))]
        for name in candidates:
            command += ["--candidate", name]
        completed = subprocess.run([*command, *extra], env=env, cwd=ROOT,
                                   text=True, capture_output=True, timeout=60)
        self.assertEqual(completed.returncode, code, completed.stderr)
        # The SDK may emit its own diagnostics before the client's final safe JSON error.
        return json.loads(output.read_text()) if code == 0 else json.loads(completed.stderr.splitlines()[-1])

    async def read_tools(self, env, run_id, foreign_id, spec):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=sys.executable, args=["-m", "proofbeak.interfaces.mcp"], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                run_tool = next(tool for tool in listing.tools if tool.name == "run_trial")
                self.assertEqual(set(run_tool.inputSchema["properties"]),
                                 {"test", "candidates", "idempotency_key"})
                history = await session.call_tool("list_runs", {})
                self.assertEqual([row["run_id"] for row in history.structuredContent["runs"]], [run_id])
                matched = await session.call_tool("find_evidence", {"test": spec, "candidates": CANDIDATES})
                self.assertTrue(matched.structuredContent["reusable"])
                self.assertEqual(matched.structuredContent["run_id"], run_id)
                fresh = await session.call_tool("find_evidence",
                    {"test": spec, "candidates": CANDIDATES, "fresh": True})
                self.assertEqual(fresh.structuredContent["reason_code"], "fresh_requested")
                self.assertIsNone(fresh.structuredContent["run_id"])
                denied = await session.call_tool("run_trial",
                    {"test": spec, "candidates": ["webdav:unallowed"], "idempotency_key": "denied"})
                self.assertTrue(denied.isError)
                for name, payload in (("get_run", {"run_id": foreign_id}),
                                      ("compare_results", {"run_id": foreign_id}),
                                      ("get_evidence", {"evidence_id": foreign_id + ":0"}),
                                      ("retry_cleanup", {"run_id": foreign_id})):
                    self.assertTrue((await session.call_tool(name, payload)).isError)
                revoked = await session.call_tool("revoke_evidence", {"run_id": run_id})
                self.assertFalse(revoked.isError)
                withdrawn = await session.call_tool("find_evidence", {"test": spec, "candidates": CANDIDATES})
                self.assertFalse(withdrawn.structuredContent["reusable"])
                repeated = await session.call_tool("run_trial",
                    {"test": spec, "candidates": CANDIDATES, "idempotency_key": "mcp-storage"})
                self.assertEqual(repeated.structuredContent["run_id"], run_id)
                comparison = await session.call_tool("compare_results", {"run_id": run_id})
                self.assertTrue(all(not row["validity"]["reusable"]
                                    for row in comparison.structuredContent["candidates"]))
                history = await session.call_tool("list_runs", {})
                self.assertEqual(len(history.structuredContent["runs"]), 1)
                audit = await session.call_tool("list_audit_events", {})
                events = [row["event"] for row in audit.structuredContent["events"]]
                for name in ("run_submitted", "run_started", "run_completed"):
                    self.assertEqual(events.count(name), 1)
                self.assertIn("evidence_revoked", events)

    def test_external_client_export_retry_reuse_and_tenant_isolation(self):
        tokens = {name: secrets.token_urlsafe(32) for name in ("a", "b")}
        providers = {"filesystem": {}, **{
            "webdav:" + name: {"endpoint": endpoint, "username": "proofbeak",
                              "password_env": "PROOFBEAK_TEST_DAV_PASSWORD"}
            for name, endpoint in (("wsgidav", self.wsgi_url), ("apache", self.apache_url))}}
        config = {"state_dir": str(self.root / "api-state"), "tenants": [{
            "id": name, "token_sha256": hashlib.sha256(tokens[name].encode()).hexdigest(),
            "providers": providers if name == "a" else {"filesystem": {}}} for name in tokens]}
        config_path = self.root / "api-config.json"
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        endpoint = f"http://127.0.0.1:{port}"
        base_env = {"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"), "PYTHONUNBUFFERED": "1",
                    "PYTHONUTF8": "1"}
        client_env = {**base_env, "PROOFBEAK_API_URL": endpoint, "PROOFBEAK_API_TOKEN": tokens["a"]}
        output = Path(os.environ.get(self.fixture_report_environment, self.root / "reports"))
        output.mkdir(parents=True, exist_ok=True)
        with (self.root / "api.log").open("w+") as log:
            api = subprocess.Popen([sys.executable, "-m", "proofbeak.server", "serve",
                "--config", str(config_path), "--port", str(port)],
                env={**base_env, "PROOFBEAK_TEST_DAV_PASSWORD": PASSWORD},
                stdout=subprocess.DEVNULL, stderr=log, cwd=ROOT)
            try:
                client = BridgeClient(endpoint, tokens["a"])
                deadline = time.monotonic() + 20
                while True:
                    try:
                        client.request("GET", "/health")
                        break
                    except ServiceError:
                        if api.poll() is not None or time.monotonic() >= deadline:
                            self.fail("actual REST service did not become ready")
                        time.sleep(.05)
                spec = json.loads((ROOT / "examples/storage.put_same_key.v1.json").read_text())
                foreign = BridgeClient(endpoint, tokens["b"]).request("POST", "/runs",
                    {"test": spec, "candidates": ["filesystem"]}, key="foreign")
                self.assertTrue(all(not rows for rows in self.mutations().values()))
                first = self.client(client_env, output / "mcp-comparison.json")
                self.assertEqual([row["provider"] for row in first["candidates"]], CANDIDATES)
                for row in first["candidates"]:
                    self.assertEqual(row["verdict"], "pass")
                    self.assertEqual(row["cleanup_status"], "cleaned")
                    self.assertEqual(row["evidence"]["before"]["objects"], row["evidence"]["after"]["objects"])
                first_journal = self.wait_mutations(5)
                for rows in first_journal.values():
                    self.assertEqual(Counter(row["method"] for row in rows),
                                     {"MKCOL": 1, "PUT": 3, "DELETE": 1})
                    self.assertTrue(all(row["path"].startswith(
                        "/proofbeak-" + first["execution_run_id"] + "/") for row in rows))
                repeated = self.client(client_env, output / "mcp-retry.json")
                self.assertEqual(repeated["run_id"], first["run_id"])
                for original, again in zip(first["candidates"], repeated["candidates"]):
                    for field in ("evidence", "evidence_hash", "observed_at", "measurements"):
                        self.assertEqual(original[field], again[field])
                denied = self.client(client_env, output / "denied.json",
                                     candidates=["webdav:unallowed"], code=3)
                self.assertEqual(denied["error"], "candidate_not_allowed")
                invalid = self.client(client_env, output / "invalid.json", extra=("--timeout", "0"), code=3)
                self.assertEqual(invalid["error"], "invalid_timeout_or_key")
                asyncio.run(self.read_tools(client_env, first["run_id"], foreign["run_id"], spec))
                self.assertEqual(self.mutations(), first_journal)
                self.assertEqual(len(client.request("GET", "/runs")["runs"]), 1)
                fresh = self.client(client_env, output / "mcp-fresh.json", key="explicit-new-trial")
                self.assertNotEqual(fresh["run_id"], first["run_id"])
                self.assertNotEqual(fresh["execution_run_id"], first["execution_run_id"])
                final_journal = self.wait_mutations(10)
                for name, rows in final_journal.items():
                    self.assertEqual(rows[:5], first_journal[name])
                    self.assertTrue(all(row["path"].startswith(
                        "/proofbeak-" + fresh["execution_run_id"] + "/") for row in rows[5:]))
                self.assertEqual(len(client.request("GET", "/runs")["runs"]), 2)
                # Restore the coordinator to its original path, keeping the same actual
                # provider accounts. Historical reads must remain mutation-free.
                api.terminate()
                api.wait(timeout=10)
                state = self.root / "api-state"
                backup(state, self.root / "snapshot")
                state.rename(self.root / "retired-api-state")
                restored = restore(self.root / "snapshot", state)
                api = subprocess.Popen([sys.executable, "-m", "proofbeak.server", "serve",
                    "--config", str(config_path), "--port", str(port)],
                    env={**base_env, "PROOFBEAK_TEST_DAV_PASSWORD": PASSWORD},
                    stdout=subprocess.DEVNULL, stderr=log, cwd=ROOT)
                deadline = time.monotonic() + 20
                while True:
                    try:
                        client.request("GET", "/health")
                        break
                    except ServiceError:
                        if api.poll() is not None or time.monotonic() >= deadline:
                            self.fail("restored REST service did not become ready")
                        time.sleep(.05)
                historical = self.client(client_env, output / "mcp-restored-history.json", key="explicit-new-trial")
                self.assertEqual(historical["run_id"], fresh["run_id"])
                for original, again in zip(fresh["candidates"], historical["candidates"]):
                    self.assertEqual(original["evidence_hash"], again["evidence_hash"])
                    self.assertEqual(original["observed_at"], again["observed_at"])
                stale = self.client(client_env, output / "stale-after-restore.json", key="unknown-old-key", code=3)
                self.assertEqual(stale["error"], "restore_key_prefix_required")
                self.assertEqual(stale["idempotency_key_prefix"], restored["idempotency_key_prefix"])
                self.assertEqual(self.mutations(), final_journal)
                restored_trial = self.client(client_env, output / "mcp-restored-new-trial.json",
                                             key=restored["idempotency_key_prefix"] + "explicit-new-trial")
                self.assertNotEqual(restored_trial["run_id"], fresh["run_id"])
                after_restore = self.wait_mutations(15)
                for name, rows in after_restore.items():
                    self.assertEqual(rows[:10], final_journal[name])
                    self.assertTrue(all(row["path"].startswith(
                        "/proofbeak-" + restored_trial["execution_run_id"] + "/") for row in rows[10:]))
                verification = {
                    "scope": "actual MCP stdio -> REST -> subprocess -> WsgiDAV and Apache",
                    "first_run_id": first["run_id"], "retry_run_id": repeated["run_id"],
                    "fresh_run_id": fresh["run_id"],
                    "first_mutations_per_provider": 5, "retry_and_read_mutation_delta": 0,
                    "explicit_new_key_mutation_delta_per_provider": 5,
                    "foreign_tenant_access": "rejected", "unallowed_candidate": "rejected",
                    "lookup_fresh_flag_created_job": False, "revocation_blocks_reuse": True,
                    "restore_history_mutation_delta": 0, "restore_stale_key": "rejected",
                    "restore_new_key_mutation_delta_per_provider": 5,
                    "restored_run_id": restored_trial["run_id"], "journals": after_restore,
                    "limits": ["No LLM was invoked; this verifies the protocol and application workflow.",
                               "HTTP mutation counts do not establish internal disk-write counts or exact-once semantics."]}
                (output / "workflow-verification.json").write_text(json.dumps(verification, indent=2) + "\n")
                for path in output.iterdir():
                    if path.suffix in {".json", ".html"}:
                        content = path.read_text()
                        self.assertTrue(all(token not in content for token in tokens.values()))
                        self.assertNotIn(PASSWORD, content)
            finally:
                api.terminate()
                api.wait(timeout=10)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(McpStorageIntegrationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    required = os.environ.get("PROOFBEAK_REQUIRE_MCP_STORAGE") == "1"
    raise SystemExit(0 if result.wasSuccessful() and not (required and result.skipped) else 1)
