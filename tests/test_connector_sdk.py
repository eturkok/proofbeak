import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from proofbeak.connector_sdk import list_plugins, load_plugin, qualify, registered_plugins, scaffold
from proofbeak.connectors import Connector, configured_connector


class SDKTests(unittest.TestCase):
    def test_scaffold_has_real_entry_point_profile_and_scenario_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "connector"
            result = scaffold(target, "my-service")
            self.assertEqual(result["kind"], "plugin:my-service")
            self.assertIn('[project.entry-points."proofbeak.connectors"]', (target / "pyproject.toml").read_text())
            self.assertEqual(json.loads((target / "scenario.json").read_text())["id"], "scenario.v2")
            with self.assertRaises(ValueError):
                scaffold(target, "my-service")
            with self.assertRaises(ValueError):
                scaffold(Path(directory) / "unsafe", "../../unsafe")

    def test_discovery_does_not_load_code_and_duplicate_names_are_rejected(self):
        loaded = []
        point = SimpleNamespace(name="sample", dist=SimpleNamespace(metadata={"Name": "sample-distribution"}, version="1.0"),
                                load=lambda: loaded.append(True) or Connector)
        with patch.object(importlib.metadata, "entry_points", return_value=[point]):
            self.assertEqual(list_plugins()[0]["version"], "1.0")
            self.assertEqual(loaded, [])
            constructor, metadata = load_plugin("sample")
            self.assertIs(constructor, Connector)
            self.assertEqual(metadata["distribution"], "sample-distribution")
        with patch.object(importlib.metadata, "entry_points", return_value=[point, point]):
            with self.assertRaises(ValueError):
                registered_plugins()

    def test_missing_or_wrong_type_plugins_do_not_become_import_paths(self):
        with patch.object(importlib.metadata, "entry_points", return_value=[]):
            for name in ("missing", "os:system", "../../module"):
                with self.assertRaises(ValueError):
                    load_plugin(name)
        point = SimpleNamespace(name="sample", dist=SimpleNamespace(metadata={"Name": "sample"}, version="1.0"), load=lambda: object)
        with patch.object(importlib.metadata, "entry_points", return_value=[point]):
            with self.assertRaises(ValueError):
                load_plugin("sample")


@unittest.skipUnless(importlib.util.find_spec("jsonschema") and os.environ.get("PROOFBEAK_REQUIRE_SDK"),
                     "explicit installed-package SDK protocol qualification runs in CI")
class InstalledSDKTests(unittest.TestCase):
    def test_installed_external_package_runs_in_the_actual_subprocess_worker(self):
        class Health(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"status":"ready"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "package"
            scaffold(project, "qualification-fixture")
            subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", str(project)], check=True, capture_output=True)
            self.addCleanup(lambda: subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "proofbeak-connector-qualification-fixture"], capture_output=True))
            server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            profiles = json.loads((project / "providers.json").read_text())
            profiles["capability:example"]["connection"]["endpoint"] = f"http://127.0.0.1:{server.server_port}"
            adapter = configured_connector("capability:example", profiles["capability:example"], root, "sdk")()
            self.assertEqual(adapter.profile["plugin"]["version"], "0.1.0")
            result = qualify(adapter.backend, json.loads((project / "scenario.json").read_text()))
            self.assertEqual(result["gate"], "pass", result)
            self.assertEqual(result["worker"], "bounded_subprocess")
            self.assertEqual(result["report"]["candidates"][0]["cleanup_status"], "cleaned")
            from proofbeak.worker_adapters import decode_adapter, encode_adapter
            self.assertEqual(decode_adapter(encode_adapter(adapter)).profile, adapter.profile)


if __name__ == "__main__":
    if os.environ.get("PROOFBEAK_REQUIRE_SDK") and not importlib.util.find_spec("jsonschema"):
        raise SystemExit("Connector SDK qualification requires connectors dependencies")
    unittest.main()
