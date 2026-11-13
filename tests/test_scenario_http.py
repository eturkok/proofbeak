"""Actual HTTP GET observations over two different response shapes; no new case-specific pack."""

import copy
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from proofbeak.adapters.scenario import JSONReadConnector, ScenarioAdapter
from proofbeak.executor import SubprocessExecutor
from proofbeak.models import fingerprint
from proofbeak.packs.scenario import ScenarioPack


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.reads.append(self.path)
        mode = self.server.mode
        body = ({"item": {"ready": True, "values": [1, 2, 3]}} if mode == "a"
                else {"data": {"usable": False, "records": [1, 2]}})
        if mode == "missing":
            body = {"item": {"values": [1, 2, 3]}}
        encoded = json.dumps(body).encode()
        if mode == "invalid":
            encoded = b'{"same":1,"same":2}'
        self.send_response(503 if mode == "unavailable" else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self):
        self.server.mutations += 1
        self.send_error(405)

    do_PUT = do_POST
    do_DELETE = do_POST

    def log_message(self, *args):
        pass


class HTTPScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servers = []
        for mode in ("a", "b"):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            except PermissionError:
                if os.environ.get("PROOFBEAK_REQUIRE_SCENARIO_HTTP") == "1":
                    raise
                raise unittest.SkipTest("TCP binding unavailable locally; scenario HTTP is mandatory in CI") from None
            server.mode, server.reads, server.mutations = mode, [], 0
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
            thread.start()
            cls.servers.append(server)
            cls.addClassCleanup(server.server_close)
            cls.addClassCleanup(server.shutdown)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for mode, server in zip(("a", "b"), self.servers):
            server.mode, server.reads, server.mutations = mode, [], 0
        self.spec = {"id": "scenario.v1", "name": "A record must be ready and have three values",
            "fixture": {"key": "sample"}, "setup": [], "steps": [],
            "observe": [{"id": "record", "op": "http.get", "args": {
                "route": "record", "params": {"key": {"ref": ["fixture", "key"]}}}}],
            "require": [], "expect": [
                {"name": "ready", "actual": ["after", "record", "json", "ready"], "op": "eq", "value": True},
                {"name": "values", "actual": ["after", "record", "json", "values"], "op": "length_eq", "value": 3}]}

    def adapters(self):
        result = []
        for mode, server in zip(("a", "b"), self.servers):
            prefix, ready, values = ("item", "ready", "values") if mode == "a" else ("data", "usable", "records")
            route = {"path": "/items/{key}" if mode == "a" else "/records/{key}",
                     "project": {"ready": [prefix, ready], "values": [prefix, values]}}
            result.append(ScenarioAdapter(JSONReadConnector("http:" + mode,
                f"http://127.0.0.1:{server.server_port}", {"record": route})))
        return result

    def run_spec(self, spec=None, key="http"):
        result = SubprocessExecutor().run(spec or self.spec, self.adapters(), self.root / "ledger.sqlite3", key)
        self.assertIsNone(result.reason, result)
        return result.payload["report"]

    def test_user_condition_compares_normalized_facts_with_original_provider_json(self):
        report = self.run_spec()
        self.assertEqual([row["verdict"] for row in report["candidates"]], ["pass", "fail"])
        for row in report["candidates"]:
            self.assertEqual(row["pack_id"], "scenario.v1")
            self.assertEqual(row["cleanup_status"], "cleaned")
            self.assertIn("raw", row["evidence"]["after"]["values"]["record"])
            self.assertEqual(row["evidence_hash"], fingerprint(row["evidence"]))
        self.assertEqual(self.servers[0].reads, ["/items/sample", "/items/sample"])
        self.assertEqual(self.servers[1].reads, ["/records/sample", "/records/sample"])
        self.assertTrue(all(server.mutations == 0 for server in self.servers))

    def test_another_condition_uses_the_same_pack_and_connectors_without_code_changes(self):
        spec = copy.deepcopy(self.spec)
        spec["name"] = "At least two values are available"
        spec["expect"] = [{"name": "minimum", "actual": ["after", "record", "json", "values"],
                           "op": "contains", "value": 2}]
        self.assertEqual([row["verdict"] for row in self.run_spec(spec)["candidates"]], ["pass", "pass"])

    def test_missing_projection_fields_cannot_pass_ne(self):
        self.servers[0].mode = "missing"
        self.spec["expect"] = [{"name": "unknown", "actual": ["after", "record", "json", "ready"],
                                "op": "ne", "value": False}]
        self.assertEqual(self.run_spec()["candidates"][0]["verdict"], "inconclusive")

    def test_permission_or_format_errors_are_inconclusive(self):
        self.servers[0].mode = "unavailable"
        self.servers[1].mode = "invalid"
        self.assertEqual([row["verdict"] for row in self.run_spec()["candidates"]],
                         ["inconclusive", "inconclusive"])

    def test_candidate_route_preflight_rejects_bad_parameter_shapes(self):
        self.spec["observe"][0]["args"]["route"] = "unconfigured"
        trial = ScenarioPack().parse(self.spec)
        for adapter in self.adapters():
            with self.assertRaises(ValueError):
                adapter.validate_scenario(trial)
        self.assertTrue(all(not server.reads for server in self.servers))
