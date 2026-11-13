"""Fresh CLI configuration -> actual MCP client -> workers -> real services -> evidence."""

import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from collections import Counter
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from acceptance_cases import (HTTP_CANDIDATES, MANIFEST, OBJECT_CANDIDATES, ROOT,
                              http_profiles, load_cases, render_summary, selection)
from proofbeak import __version__
from proofbeak.interfaces.mcp import BridgeClient
from proofbeak.models import fingerprint
from proofbeak.packs.scenario import resolve
from proofbeak.service import ServiceError
from webdav_fixtures import AVAILABLE, PASSWORD, WebDAVFixtures

AGENTS = all(importlib.util.find_spec(name) for name in ("mcp", "fastapi", "uvicorn", "httpx"))


class CatalogHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.reads.append(self.path)
        first = self.server.catalog == "a"
        if self.path != ("/v1/capabilities" if first else "/v2/meta"):
            self.send_error(404)
            return
        data = ({"service": {"ready": True, "capacity": 500, "retention_days": 30}} if first else
                {"api": {"enabled": False, "limit": 2500}})
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.server.mutations.append(self.command)
        self.send_error(405)

    do_PUT = do_POST
    do_DELETE = do_POST

    def log_message(self, *args):
        pass


@unittest.skipUnless(AVAILABLE and AGENTS, "requires actual WebDAV fixture images and agents dependencies")
class AcceptanceIntegrationTests(WebDAVFixtures, unittest.TestCase):
    fixture_report_environment = "PROOFBEAK_ACCEPTANCE_REPORT_DIR"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.catalogs, cls.origins = [], []
        for name in ("a", "b"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), CatalogHandler)
            server.catalog, server.reads, server.mutations = name, [], []
            cls.addClassCleanup(server.server_close)
            cls.addClassCleanup(server.shutdown)
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True).start()
            cls.catalogs.append(server)
            cls.origins.append(f"http://127.0.0.1:{server.server_port}")

    def start_api(self):
        self.api = subprocess.Popen([sys.executable, "-m", "proofbeak.server", "serve", "--config", str(self.config),
            "--port", str(self.port)], cwd=ROOT, env=self.api_env, stdout=subprocess.DEVNULL, stderr=self.api_log)
        deadline = time.monotonic() + 20
        while True:
            try:
                self.client.request("GET", "/health")
                return
            except ServiceError:
                if self.api.poll() is not None or time.monotonic() >= deadline:
                    self.fail("fresh acceptance API did not become ready")
                time.sleep(.05)

    def stop_api(self):
        if self.api is not None:
            self.api.terminate()
            self.api.wait(timeout=10)
            self.api = None

    def run_client(self, case, output, key):
        command = [sys.executable, str(ROOT / "examples/mcp_compare.py"), "--spec", str(case["path"]),
                   "--key", key, "--output", str(output), "--html", str(output.with_suffix(".html")), "--timeout", "60"]
        for name in case["candidates"]:
            command += ["--candidate", name]
        completed = subprocess.run(command, cwd=ROOT, env=self.client_env, capture_output=True, text=True, timeout=90)
        verdicts = set(case["expected"].values())
        expected_exit = 2 if "inconclusive" in verdicts else (1 if "fail" in verdicts else 0)
        self.assertEqual(completed.returncode, expected_exit, "unexpected client exit for " + case["id"])
        report = json.loads(output.read_text())
        self.assertEqual(report["test"], case["spec"])
        self.assertEqual({row["provider"]: row["verdict"] for row in report["candidates"]}, case["expected"])
        for row in report["candidates"]:
            self.assertEqual(row["cleanup_status"], "cleaned")
            self.assertEqual(row["test_hash"], case["test_hash"])
            self.assertEqual(fingerprint(row["evidence"]), row["evidence_hash"])
            self.assertIsNotNone(row["evidence"]["before"])
            self.assertIsNotNone(row["evidence"]["after"])
        return report

    def wait_journals(self, expected):
        deadline = time.monotonic() + 5
        while True:
            rows = self.mutations()
            if all(len(items) >= expected for items in rows.values()):
                self.assertEqual({name: len(items) for name, items in rows.items()}, {"wsgidav": expected, "apache": expected})
                return rows
            if time.monotonic() >= deadline:
                self.fail("acceptance provider journals did not reach their expected count")
            time.sleep(.05)

    def assert_stored_report_unchanged(self, actual, original):
        reports = []
        for report in (actual, original):
            copied = json.loads(json.dumps(report))
            for row in copied["candidates"]:
                # A read re-evaluates current validity; it does not change trial observations.
                self.assertIsInstance(row["validity"].pop("checked_at"), str)
            reports.append(copied)
        self.assertEqual(*reports)

    def test_twelve_selection_questions_from_fresh_configuration(self):
        manifest, cases = load_cases()
        output = Path(os.environ.get(self.fixture_report_environment, self.root / "reports"))
        output.mkdir(parents=True, exist_ok=True)
        base_env = {"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        self.api_env = {**base_env, "ACCEPTANCE_WEBDAV_PASSWORD": PASSWORD}
        profiles = {name: {"endpoint": endpoint, "username": "proofbeak", "password_env": "ACCEPTANCE_WEBDAV_PASSWORD"}
                    for name, endpoint in zip(OBJECT_CANDIDATES, (self.wsgi_url, self.apache_url))}
        profiles.update(http_profiles(self.origins))
        providers = self.root / "providers.json"
        providers.write_text(json.dumps(profiles))
        self.config = self.root / "server.json"
        initialized = subprocess.run([sys.executable, "-m", "proofbeak.server", "init", "--config", str(self.config),
            "--state", str(self.root / "api-state"), "--providers", str(providers), "--tenant", "acceptance-user"],
            cwd=ROOT, env=base_env, capture_output=True, text=True, timeout=20)
        self.assertEqual(initialized.returncode, 0)
        token = next(line.removeprefix("API key (shown once): ") for line in initialized.stdout.splitlines()
                     if line.startswith("API key (shown once): "))
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        checked = subprocess.run([sys.executable, "-m", "proofbeak.server", "check", "--config", str(self.config),
            "--require-providers"], cwd=ROOT, env=self.api_env, capture_output=True, text=True, timeout=20)
        self.assertEqual(checked.returncode, 0)
        self.assertTrue(json.loads(checked.stdout)["providers_configured"])
        self.assertTrue(all(not rows for rows in self.mutations().values()))
        self.assertTrue(all(not server.reads for server in self.catalogs))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        origin = f"http://127.0.0.1:{self.port}"
        self.client = BridgeClient(origin, token)
        self.client_env = {**base_env, "PROOFBEAK_API_URL": origin, "PROOFBEAK_API_TOKEN": token}
        self.api_log = (self.root / "api.log").open("a")
        self.addCleanup(self.api_log.close)
        self.api = None
        self.addCleanup(self.stop_api)
        self.start_api()
        records, original, total = [], None, 0
        previous = {name: [] for name in ("wsgidav", "apache")}
        for case in cases:
            with self.subTest(case=case["id"]):
                validation = self.client.request("POST", "/trials/validate", {"test": case["spec"], "candidates": case["candidates"]})
                self.assertTrue(validation["valid"])
                self.assertFalse(validation["execution_performed"])
                report_path = output / (case["id"] + ".json")
                report = self.run_client(case, report_path, "acceptance-" + case["id"])
                if original is None:
                    original = report
                if case["candidates"] == OBJECT_CANDIDATES:
                    operations = [step["op"] for phase in ("setup", "steps") for step in case["spec"][phase]]
                    expected = Counter({"MKCOL": 1, "PUT": 1 + operations.count("object.put"),
                                        "DELETE": 1 + operations.count("object.delete")})
                    total += sum(expected.values())
                    journal = self.wait_journals(total)
                    deleted_keys = {resolve(step["args"]["key"], {"fixture": case["spec"]["fixture"]})
                                    for phase in ("setup", "steps") for step in case["spec"][phase]
                                    if step["op"] == "object.delete"}
                    for name, rows in journal.items():
                        added = rows[len(previous[name]):]
                        self.assertEqual(Counter(row["method"] for row in added), expected)
                        self.assertTrue(all(200 <= row["status"] < 300 or (row["method"] == "DELETE"
                            and row["status"] == 404 and row["path"].rsplit("/", 1)[-1] in deleted_keys) for row in added))
                        self.assertTrue(all(row["path"].startswith("/proofbeak-" + report["execution_run_id"] + "/") for row in added))
                    previous = journal
                else:
                    self.assertEqual(self.mutations(), previous)
                observed = {row["provider"]: row["verdict"] for row in report["candidates"]}
                records.append({"case_id": case["id"], "question": case["question"], "test_hash": case["test_hash"],
                    "run_id": report["run_id"], "expected": case["expected"], "observed": observed, **selection(observed),
                    "cleanup": {row["provider"]: row["cleanup_status"] for row in report["candidates"]},
                    "evidence_hashes": {row["provider"]: row["evidence_hash"] for row in report["candidates"]},
                    "report_json": report_path.name, "report_html": report_path.with_suffix(".html").name,
                    "report_file_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest()})
        self.assertEqual(len(records), 12)
        expected_paths = [["/v1/capabilities"] * 8, ["/v2/meta"] * 8]
        self.assertEqual([server.reads for server in self.catalogs], expected_paths)
        self.assertTrue(all(not server.mutations for server in self.catalogs))
        self.assertEqual(len(self.client.request("GET", "/runs")["runs"]), 12)
        retry = self.run_client(cases[0], output / "same-key-retrieval.json", "acceptance-" + cases[0]["id"])
        self.assert_stored_report_unchanged(retry, original)
        self.stop_api()
        self.start_api()
        restarted = self.run_client(cases[0], output / "after-restart-retrieval.json", "acceptance-" + cases[0]["id"])
        self.assert_stored_report_unchanged(restarted, original)
        self.assertEqual(self.mutations(), previous)
        self.assertEqual([server.reads for server in self.catalogs], expected_paths)
        counts = dict(Counter(verdict for row in records for verdict in row["observed"].values()))
        self.assertEqual(counts, {"pass": 17, "fail": 4, "inconclusive": 3})
        summary = {"evaluation_id": manifest["id"], "application_version": __version__, "recorded_at": datetime.now(UTC).isoformat(),
            "ci_run_id": os.environ.get("GITHUB_RUN_ID"), "source_commit": os.environ.get("GITHUB_SHA"),
            "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), "case_count": len(records),
            "candidate_observations": 24, "verdict_counts": counts, "all_cleanup_completed": True,
            "provider_mutation_requests": {name: len(rows) for name, rows in previous.items()},
            "http_reads": {name: len(server.reads) for name, server in zip(HTTP_CANDIDATES, self.catalogs)},
            "checks": {"fresh_cli_configuration": True, "offline_config_check": True, "same_key_stored_result_unchanged": True,
                       "restart_stored_result_unchanged": True, "retry_or_restart_mutations": 0, "evidence_hashes_verified": True},
            "limits": ["Author-prepared synthetic needs and HTTP fixtures, not independent human pilot data.",
                       "No external model calls, A/B agent scoring, human onboarding or return-usage measurements.",
                       "Eligibility applies to each definition only; combine conditions when all are required."],
            "cases": records}
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        (output / "summary.html").write_text(render_summary(summary))
        (output / "provider-journals.json").write_text(json.dumps(previous, indent=2) + "\n")
        for path in output.iterdir():
            if path.suffix in {".json", ".html"}:
                self.assertNotIn(token, path.read_text())
                self.assertNotIn(PASSWORD, path.read_text())
        print("PROOFBEAK_ACCEPTANCE_SUMMARY=" + json.dumps(summary, separators=(",", ":")))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AcceptanceIntegrationTests))
    required = os.environ.get("PROOFBEAK_REQUIRE_ACCEPTANCE") == "1"
    raise SystemExit(0 if result.wasSuccessful() and not (required and result.skipped) else 1)
