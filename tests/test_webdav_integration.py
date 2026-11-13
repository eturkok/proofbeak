"""Actual WsgiDAV and Apache round trips; no cloud account and no protocol mock."""

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from proofbeak.adapters.base import ProviderError
from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.webdav import WebDAVAdapter
from proofbeak.engine import recover_cleanup, run_trial
from proofbeak.executor import SubprocessExecutor
from proofbeak.report import render_html
from proofbeak.store import Store

AVAILABLE = all(importlib.util.find_spec(name) for name in ("wsgidav", "cheroot")) \
    and shutil.which("docker") is not None
PASSWORD = "proofbeak-local-only"  # Public disposable fixture credential, never a real account.
IMAGE = os.environ.get("PROOFBEAK_WEBDAV_APACHE_IMAGE", "proofbeak-webdav-apache:test")


def wait_for(endpoint, process=None):
    adapter = WebDAVAdapter("webdav:ready", endpoint, "proofbeak", PASSWORD)
    deadline = time.monotonic() + 20
    while True:
        try:
            adapter.transport.calls = 0
            adapter.transport.request("OPTIONS", endpoint + "/", headers=adapter.headers, allowed=(200, 204))
            return
        except ProviderError:
            if (process and process.poll() is not None) or time.monotonic() > deadline:
                raise RuntimeError("actual WebDAV fixture did not become ready") from None
            time.sleep(.1)


@unittest.skipUnless(AVAILABLE, "install WebDAV fixture dependencies and run Docker for integration")
class WebDAVIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        checked = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, text=True)
        if checked.returncode:
            raise unittest.SkipTest("build deploy/Dockerfile.webdav-apache before integration")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        objects = cls.root / "wsgi-objects"
        objects.mkdir()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.log = (cls.root / "wsgi.log").open("w+")
        cls.addClassCleanup(cls.log.close)
        cls.wsgi = subprocess.Popen([sys.executable, "deploy/webdav_wsgi.py"], stdin=subprocess.PIPE,
            stdout=cls.log, stderr=cls.log, text=True)
        def stop_wsgi():
            cls.wsgi.terminate()
            try:
                cls.wsgi.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.wsgi.kill()
                cls.wsgi.wait(timeout=5)
        cls.addClassCleanup(stop_wsgi)
        cls.wsgi.stdin.write(json.dumps({"root": str(objects), "host": "127.0.0.1",
                                         "port": port, "password": PASSWORD}))
        cls.wsgi.stdin.close()
        cls.wsgi_url = f"http://127.0.0.1:{port}"
        wait_for(cls.wsgi_url, cls.wsgi)
        cls.container = subprocess.check_output(["docker", "run", "-d", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--tmpfs", "/tmp:uid=10001,gid=10001,mode=0700,size=64m",
            "--tmpfs", "/usr/local/apache2/htdocs:uid=10001,gid=10001,mode=0700,size=32m",
            "-p", "127.0.0.1::8080", IMAGE], text=True).strip()
        cls.addClassCleanup(lambda: subprocess.run(["docker", "rm", "-f", cls.container],
                                                  check=True, stdout=subprocess.DEVNULL))
        metadata = json.loads(subprocess.check_output(["docker", "inspect", cls.container], text=True))[0]
        port = metadata["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"]
        cls.apache_url = f"http://127.0.0.1:{port}"
        wait_for(cls.apache_url)

    def adapters(self):
        return [WebDAVAdapter("webdav:wsgidav", self.wsgi_url, "proofbeak", PASSWORD),
                WebDAVAdapter("webdav:apache", self.apache_url, "proofbeak", PASSWORD)]

    def compare(self, label, content):
        spec = json.loads(Path("examples/storage.put_same_key.v1.json").read_text())
        spec["fixture"]["content"] = content
        executor = SubprocessExecutor()
        adapters = self.adapters()
        adapters.append(FilesystemAdapter(self.root / "filesystem"))
        ledger = self.root / (label + ".sqlite3")
        result = executor.run(spec, adapters, ledger, label)
        self.assertIsNone(result.reason, result)
        report = result.payload["report"]
        for candidate in report["candidates"]:
            self.assertEqual(candidate["verdict"], "pass", candidate)
            self.assertEqual(candidate["cleanup_status"], "cleaned")
            self.assertTrue(candidate["eligible_for_provider_comparison"])
            self.assertEqual(candidate["evidence"]["before"]["objects"],
                             candidate["evidence"]["after"]["objects"])
            if candidate["provider"].startswith("webdav:"):
                self.assertGreater(candidate["measurements"]["provider_http_attempts"], 0)
        again = executor.run(spec, self.adapters() + [FilesystemAdapter(self.root / "filesystem")],
                             ledger, label)
        self.assertIsNone(again.reason)
        self.assertEqual(report, again.payload["report"])
        with closing(Store(ledger)) as store:
            self.assertEqual(store.pending(), [])
        for adapter in self.adapters():
            resource = adapter.resource(report["run_id"])
            self.assertEqual(adapter._request(resource, "PROPFIND", headers={"Depth": "0"},
                                             allowed=(404,)).status, 404)
        if os.environ.get("PROOFBEAK_WEBDAV_REPORT_DIR"):
            root = Path(os.environ["PROOFBEAK_WEBDAV_REPORT_DIR"])
            root.mkdir(parents=True, exist_ok=True)
            path = root / (label + ".json")
            path.write_text(json.dumps(report, indent=2) + "\n")
            path.with_suffix(".html").write_text(render_html(report))

    def test_actual_three_candidate_comparison(self):
        self.compare("storage-comparison", "Proofbeak disposable repeated upload")

    def test_actual_unicode_content_comparison(self):
        self.compare("storage-comparison-utf8", "Tek anahtar, aynı içerik: üğşıöç ✓")

    def test_bad_credentials_remain_inconclusive_and_can_recover_with_correct_credentials(self):
        for adapter in self.adapters():
            with self.subTest(provider=adapter.name):
                wrong = WebDAVAdapter(adapter.name, adapter.endpoint, "proofbeak", "wrong-fixture-password")
                spec = json.loads(Path("examples/storage.put_same_key.v1.json").read_text())
                with closing(Store(self.root / (adapter.name.replace(":", "-") + "-denied.sqlite3"))) as store:
                    report = run_trial(spec, [wrong], store, "denied")
                    candidate = report["candidates"][0]
                    self.assertEqual(candidate["verdict"], "inconclusive")
                    self.assertEqual(candidate["reason_code"], "permission_denied")
                    self.assertEqual(recover_cleanup(store, [adapter])[0]["status"], "cleaned")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(WebDAVIntegrationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    required = os.environ.get("PROOFBEAK_REQUIRE_WEBDAV") == "1"
    raise SystemExit(0 if result.wasSuccessful() and not (required and result.skipped) else 1)
