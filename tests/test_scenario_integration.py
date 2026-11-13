"""Users change definitions, not Python: real MCP/REST/workers and two actual WebDAV services."""

import asyncio
import copy
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
from proofbeak.models import fingerprint
from proofbeak.report import render_html
from proofbeak.service import ServiceError
from webdav_fixtures import AVAILABLE, PASSWORD, WebDAVFixtures

AGENTS = all(importlib.util.find_spec(name) for name in ("mcp", "fastapi", "uvicorn", "httpx", "jsonschema"))
ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ["scenario:webdav:wsgidav", "scenario:webdav:apache"]


@unittest.skipUnless(AVAILABLE and AGENTS, "build real WebDAV fixtures and install agents dependencies")
class ScenarioIntegrationTests(WebDAVFixtures, unittest.TestCase):
    fixture_report_environment = "PROOFBEAK_SCENARIO_REPORT_DIR"

    def test_user_authored_conditions_through_actual_mcp(self):
        token = secrets.token_urlsafe(32)
        config = {"state_dir": str(self.root / "api-state"), "tenants": [{
            "id": "scenario-user", "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "providers": {"scenario:webdav:" + name: {
                "endpoint": endpoint, "username": "proofbeak", "password_env": "SCENARIO_DAV_PASSWORD"}
                for name, endpoint in (("wsgidav", self.wsgi_url), ("apache", self.apache_url))}}]}
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        base_env = {"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1",
                    "PYTHONUNBUFFERED": "1"}
        bridge_env = {**base_env, "PROOFBEAK_API_URL": origin, "PROOFBEAK_API_TOKEN": token}
        output = Path(os.environ.get(self.fixture_report_environment, self.root / "reports"))
        output.mkdir(parents=True, exist_ok=True)
        with (self.root / "api.log").open("w+") as log:
            api = subprocess.Popen([sys.executable, "-m", "proofbeak.server", "serve",
                "--config", str(config_path), "--port", str(port)], cwd=ROOT,
                env={**base_env, "SCENARIO_DAV_PASSWORD": PASSWORD},
                stdout=subprocess.DEVNULL, stderr=log)
            try:
                client = BridgeClient(origin, token)
                deadline = time.monotonic() + 20
                while True:
                    try:
                        client.request("GET", "/health")
                        break
                    except ServiceError:
                        if api.poll() is not None or time.monotonic() >= deadline:
                            self.fail("scenario REST server did not become ready")
                        time.sleep(.05)
                asyncio.run(self.session(bridge_env, output))
                for path in output.iterdir():
                    if path.suffix in {".json", ".html"}:
                        self.assertNotIn(token, path.read_text())
                        self.assertNotIn(PASSWORD, path.read_text())
            finally:
                api.terminate()
                api.wait(timeout=10)

    async def session(self, environment, output):
        from jsonschema import Draft202012Validator
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        parameters = StdioServerParameters(command=sys.executable,
            args=["-m", "proofbeak.interfaces.mcp"], env=environment)
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                validation_tool = next(tool for tool in tools.tools if tool.name == "validate_trial")
                self.assertTrue(validation_tool.annotations.readOnlyHint)

                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    self.assertFalse(result.isError, name)
                    self.assertIsInstance(result.structuredContent, dict)
                    return result.structuredContent

                catalog = await call("list_trial_packs", {})
                definition = next(p for p in catalog["packages"] if p["id"] == "scenario.v1")
                Draft202012Validator.check_schema(definition["input_schema"])
                validator = Draft202012Validator(definition["input_schema"])
                validator.validate(definition["example_test"])
                for candidate in CANDIDATES:
                    operations = catalog["provider_capabilities"][candidate]["operations"]
                    self.assertEqual({op["id"] for op in operations},
                                     {"object.put", "object.get", "object.list", "object.delete"})
                    self.assertTrue(next(op for op in operations if op["id"] == "object.get")["read_only"])

                definitions = [(name, json.loads((ROOT / "examples" / ("scenario." + name + ".json")).read_text()))
                    for name in ("delete_and_preserve", "update_and_preserve", "repeat_put")]
                incorrect = copy.deepcopy(definitions[0][1])
                incorrect["name"] = "Caller changes the expected remaining count"
                incorrect["expect"][1]["value"] = 7
                definitions.append(("user_changed_expectation", incorrect))
                for _, spec in definitions:
                    validator.validate(spec)
                    validation = await call("validate_trial", {"test": spec, "candidates": CANDIDATES})
                    self.assertTrue(validation["valid"])
                    self.assertFalse(validation["execution_performed"])
                    self.assertFalse(validation["provider_access_checked"])
                invalid = copy.deepcopy(definitions[0][1])
                invalid["steps"][0]["op"] = "shell.exec"
                validation = await call("validate_trial", {"test": invalid, "candidates": CANDIDATES})
                self.assertFalse(validation["valid"])
                self.assertTrue(validation["issues"])
                self.assertEqual((await call("list_runs", {}))["runs"], [])
                self.assertTrue(all(not rows for rows in self.mutations().values()))

                records, previous = [], {name: [] for name in ("wsgidav", "apache")}
                for index, (label, spec) in enumerate(definitions):
                    key = "scenario-" + label
                    submitted = await call("run_trial", {"test": spec, "candidates": CANDIDATES, "idempotency_key": key})
                    run_id = submitted["run_id"]
                    deadline = time.monotonic() + 30
                    while True:
                        state = await call("get_run", {"run_id": run_id})
                        if state["state"] in {"completed", "interrupted", "cancelled"}:
                            break
                        self.assertLess(time.monotonic(), deadline)
                        await asyncio.sleep(.05)
                    self.assertEqual(state["state"], "completed")
                    report = await call("compare_results", {"run_id": run_id})
                    self.assertEqual(report["test"], spec)
                    self.assertEqual([row["provider"] for row in report["candidates"]], CANDIDATES)
                    expected = "fail" if label == "user_changed_expectation" else "pass"
                    for row in report["candidates"]:
                        self.assertEqual(row["pack_id"], "scenario.v1")
                        self.assertEqual(row["verdict"], expected)
                        self.assertEqual(row["cleanup_status"], "cleaned")
                        evidence = await call("get_evidence", {"evidence_id": row["evidence_ref"]})
                        row["evidence"] = evidence["evidence"]
                        self.assertEqual(fingerprint(row["evidence"]), row["evidence_hash"])
                        phases = {event["phase"] for event in row["evidence"]["after"]["events"]}
                        self.assertTrue({"setup", "observe_before", "steps", "observe_after"} <= phases)
                        self.assertTrue(any(event["read_only"] for event in row["evidence"]["after"]["events"]))
                    journal = self.mutations()
                    for name, rows in journal.items():
                        self.assertEqual(rows[:len(previous[name])], previous[name])
                        added = rows[len(previous[name]):]
                        self.assertEqual(len(added), 6)
                        self.assertTrue(all(200 <= row["status"] < 300 for row in added))
                        self.assertTrue(all(row["path"].startswith(
                            "/proofbeak-" + report["execution_run_id"] + "/") for row in added))
                        counts = Counter(row["method"] for row in added)
                        self.assertEqual(counts, {"MKCOL": 1, "PUT": 3, "DELETE": 2} if "delete" in label
                                         or label == "user_changed_expectation" else {"MKCOL": 1, "PUT": 4, "DELETE": 1})
                    repeated = await call("run_trial", {"test": spec, "candidates": CANDIDATES, "idempotency_key": key})
                    self.assertEqual(repeated["run_id"], run_id)
                    await call("validate_trial", {"test": spec, "candidates": CANDIDATES})
                    await call("find_evidence", {"test": spec, "candidates": CANDIDATES})
                    self.assertEqual(self.mutations(), journal)
                    changed = copy.deepcopy(spec)
                    changed["name"] += " changed"
                    conflict = await session.call_tool("run_trial", {
                        "test": changed, "candidates": CANDIDATES, "idempotency_key": key})
                    self.assertTrue(conflict.isError)
                    self.assertEqual(self.mutations(), journal)
                    previous = journal
                    (output / (label + ".json")).write_text(json.dumps(report, indent=2) + "\n")
                    (output / (label + ".html")).write_text(render_html(report))
                    records.append({"definition": label, "run_id": run_id, "verdict": expected,
                                    "pack_id": "scenario.v1", "mutations_per_provider": 6})
                self.assertEqual(len({row["run_id"] for row in records}), 4)
                self.assertEqual(len((await call("list_runs", {}))["runs"]), 4)
                self.assertEqual({name: len(rows) for name, rows in previous.items()}, {"wsgidav": 24, "apache": 24})
                (output / "scenario-verification.json").write_text(json.dumps({
                    "scope": "actual MCP -> REST -> bounded worker -> WsgiDAV and Apache",
                    "user_definitions": records, "python_case_registration_changes": 0,
                    "validation_created_jobs": False, "retry_read_validation_mutation_delta": 0,
                    "provider_journals": previous,
                    "limits": ["Synthetic fixtures, not a measured improvement in agent decisions.",
                               "HTTP counts do not establish internal disk writes or exact-once execution."]}, indent=2) + "\n")


if __name__ == "__main__":
    outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ScenarioIntegrationTests))
    required = os.environ.get("PROOFBEAK_REQUIRE_SCENARIOS") == "1"
    raise SystemExit(0 if outcome.wasSuccessful() and not (required and outcome.skipped) else 1)
