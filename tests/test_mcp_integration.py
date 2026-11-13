"""Real MCP stdio session -> real loopback REST server -> actual filesystem trial."""

import asyncio
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

AVAILABLE = all(importlib.util.find_spec(name) for name in ("mcp", "fastapi", "uvicorn", "httpx"))
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(AVAILABLE, "install .[agents] for the real MCP protocol test")
class McpIntegrationTests(unittest.TestCase):
    def test_stdio_tools_and_tenant_bound_evidence(self):
        import httpx

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            tokens = {"a": "a" * 40, "b": "b" * 40}
            config = {"state_dir": str(root / "state"), "tenants": [{
                "id": name, "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "providers": {"filesystem": {}}} for name, token in tokens.items()]}
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config))
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            endpoint = f"http://127.0.0.1:{port}"
            with (root / "api.log").open("w+") as log:
                process = subprocess.Popen([sys.executable, "-m", "proofbeak.server", "serve",
                    "--config", str(config_path), "--port", str(port)], env=env,
                    stdout=subprocess.DEVNULL, stderr=log)
                try:
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            with urlopen(endpoint + "/health", timeout=1):
                                break
                        except URLError:
                            if process.poll() is not None or time.monotonic() > deadline:
                                log.seek(0)
                                self.fail("REST test server did not start: " + log.read())
                            time.sleep(0.1)
                    spec = json.loads((ROOT / "examples/storage.put_same_key.v1.json").read_text())
                    foreign = httpx.post(endpoint + "/runs", json={"test": spec, "candidates": ["filesystem"]},
                        headers={"Authorization": f'Bearer {tokens["b"]}', "Idempotency-Key": "foreign"})
                    self.assertEqual(foreign.status_code, 202)
                    asyncio.run(self.session(endpoint, tokens["a"], spec, foreign.json()["run_id"], env))
                finally:
                    process.terminate()
                    process.wait(timeout=10)

    async def session(self, endpoint, token, spec, foreign_id, env):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=sys.executable,
            args=["-m", "proofbeak.interfaces.mcp"],
            env={**env, "PROOFBEAK_API_URL": endpoint, "PROOFBEAK_API_TOKEN": token})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                tools = {tool.name: tool for tool in listing.tools}
                self.assertEqual(set(tools), {"list_trial_packs", "run_trial", "get_run",
                    "compare_results", "get_evidence", "retry_cleanup", "cancel_run", "list_runs",
                    "find_evidence", "revoke_evidence", "list_audit_events", "validate_trial",
                    "validate_suite", "run_suite", "get_suite", "list_suites", "cancel_suite", "compare_runs",
                    "get_signing_key", "export_evidence_bundle"})
                self.assertTrue(tools["validate_trial"].annotations.readOnlyHint)
                self.assertNotIn("tenant_id", tools["run_trial"].inputSchema["properties"])
                self.assertTrue(all(tool.outputSchema is not None for tool in tools.values()))
                packs = await session.call_tool("list_trial_packs", {})
                self.assertFalse(packs.isError)
                self.assertIn("filesystem", packs.structuredContent["providers"])
                spec = next(pack["example_test"] for pack in packs.structuredContent["packages"]
                            if pack["id"] == "storage.put_same_key.v1")
                submitted = await session.call_tool("run_trial", {
                    "test": spec, "candidates": ["filesystem"], "idempotency_key": "stdio-test"})
                self.assertFalse(submitted.isError)
                run_id = submitted.structuredContent["run_id"]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    run = await session.call_tool("get_run", {"run_id": run_id})
                    if run.structuredContent["state"] == "completed":
                        break
                    await asyncio.sleep(0.02)
                self.assertEqual(run.structuredContent["state"], "completed")
                comparison = await session.call_tool("compare_results", {"run_id": run_id})
                self.assertEqual(comparison.structuredContent["candidates"][0]["verdict"], "pass")
                evidence_id = comparison.structuredContent["candidates"][0]["evidence_ref"]
                evidence = await session.call_tool("get_evidence", {"evidence_id": evidence_id})
                self.assertIsNotNone(evidence.structuredContent["evidence"]["after"])
                history = await session.call_tool("list_runs", {})
                self.assertEqual([row["run_id"] for row in history.structuredContent["runs"]], [run_id])
                reusable = await session.call_tool("find_evidence", {"test": spec, "candidates": ["filesystem"]})
                self.assertTrue(reusable.structuredContent["reusable"])
                revoked = await session.call_tool("revoke_evidence", {"run_id": run_id})
                self.assertFalse(revoked.isError)
                withdrawn = await session.call_tool("find_evidence", {"test": spec, "candidates": ["filesystem"]})
                self.assertFalse(withdrawn.structuredContent["reusable"])
                audit = await session.call_tool("list_audit_events", {})
                self.assertTrue(audit.structuredContent["events"])
                repeated = await session.call_tool("run_trial", {
                    "test": spec, "candidates": ["filesystem"], "idempotency_key": "stdio-test"})
                self.assertEqual(repeated.structuredContent["run_id"], run_id)
                forbidden = await session.call_tool("get_run", {"run_id": foreign_id})
                self.assertTrue(forbidden.isError)
                forbidden_evidence = await session.call_tool("get_evidence", {"evidence_id": foreign_id + ":0"})
                self.assertTrue(forbidden_evidence.isError)
                cleanup = await session.call_tool("retry_cleanup", {"run_id": run_id})
                self.assertFalse(cleanup.isError)


if __name__ == "__main__":
    if os.environ.get("PROOFBEAK_REQUIRE_INTERFACES") == "1" and not AVAILABLE:
        raise SystemExit("MCP integration dependencies missing")
    unittest.main()
