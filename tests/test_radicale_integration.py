"""Actual Radicale round trip, not a mock. Mandatory in the integration CI job."""

import importlib.util
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from proofbeak.adapters.radicale import RadicaleAdapter
from proofbeak.engine import run_trial
from proofbeak.report import render_html
from proofbeak.store import Store

AVAILABLE = all(importlib.util.find_spec(name) for name in ("radicale", "icalendar"))


@unittest.skipUnless(AVAILABLE, "install .[calendar,dev] for a real Radicale trial")
class RadicaleIntegrationTests(unittest.TestCase):
    def test_real_recurring_exception_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            password = secrets.token_hex(16)
            (root / "users").write_text(f"proofbeak:{password}\n")
            config = root / "config"
            config.write_text(
                f"[server]\nhosts = 127.0.0.1:{port}\n[auth]\ntype = htpasswd\n"
                f"htpasswd_filename = {root / 'users'}\nhtpasswd_encryption = plain\n"
                f"[rights]\ntype = owner_only\n[storage]\nfilesystem_folder = {root / 'data'}\n")
            log = (root / "server.log").open("w+")
            with log:
                process = subprocess.Popen([sys.executable, "-m", "radicale", "--config", str(config)],
                                           stdout=log, stderr=log)
                try:
                    endpoint = f"http://127.0.0.1:{port}"
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            with urlopen(endpoint, timeout=1):
                                break
                        except URLError:
                            if process.poll() is not None or time.monotonic() > deadline:
                                log.seek(0)
                                self.fail(f"Radicale did not start: {log.read()}")
                            time.sleep(0.1)
                    spec = json.loads((Path(__file__).resolve().parents[1] /
                                       "examples/calendar.move_one.v1.json").read_text())
                    adapter = RadicaleAdapter(endpoint, "proofbeak", password)
                    store = Store(root / "runs.sqlite3")
                    try:
                        report = run_trial(spec, [adapter], store, "real-radicale")
                        result = report["candidates"][0]
                        self.assertEqual(result["verdict"], "pass", result)
                        self.assertEqual(result["cleanup_status"], "cleaned")
                        self.assertEqual(len(result["evidence"]["after"]["events"]), 4)
                        self.assertTrue(result["eligible_for_provider_comparison"])
                        self.assertEqual(store.pending(), [])
                        resource = adapter.resource(store.db.execute("SELECT id FROM runs").fetchone()[0])
                        self.assertEqual(adapter.transport.request(
                            "GET", resource.id, headers=adapter.headers, allowed=(404,)).status, 404)
                        if os.environ.get("PROOFBEAK_INTEGRATION_REPORT"):
                            output = Path(os.environ["PROOFBEAK_INTEGRATION_REPORT"])
                            output.parent.mkdir(parents=True, exist_ok=True)
                            output.write_text(json.dumps(report, indent=2) + "\n")
                            output.with_suffix(".html").write_text(render_html(report))
                    finally:
                        store.close()
                finally:
                    process.terminate()
                    process.wait(timeout=5)


if __name__ == "__main__":
    if os.environ.get("PROOFBEAK_REQUIRE_INTEGRATION") == "1" and not AVAILABLE:
        raise SystemExit("Integration dependencies missing")
    unittest.main()
