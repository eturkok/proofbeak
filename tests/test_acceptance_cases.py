"""Prepare the same inputs locally and execute the object subset against real files."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from acceptance_cases import HTTP_CANDIDATES, ROOT, http_profiles, load_cases
from proofbeak.models import fingerprint
from proofbeak.packs import builtins
from proofbeak.server import provider_factory


class AcceptanceInputsTests(unittest.TestCase):
    def test_twelve_distinct_definitions_validate_without_provider_access(self):
        _, cases = load_cases()
        self.assertEqual(len(cases), 12)
        profiles = http_profiles(["http://127.0.0.1:8803", "http://127.0.0.1:8804"])
        for case in cases:
            if case["candidates"] == HTTP_CANDIDATES:
                pack, trial = builtins().prepare(case["spec"])
                for name, settings in profiles.items():
                    adapter = provider_factory(name, settings, ROOT, "evaluation")()
                    pack.validate_adapter(trial, adapter)

    def test_object_cases_from_the_manifest_work_through_the_actual_cli(self):
        _, cases = load_cases()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PROOFBEAK_FILESYSTEM_ROOT": str(root / "objects")}
            for case in cases:
                if case["candidates"] == HTTP_CANDIDATES:
                    continue
                with self.subTest(case=case["id"]):
                    expected = next(iter(set(case["expected"].values())))
                    output = root / (case["id"] + ".json")
                    result = subprocess.run([sys.executable, "-m", "proofbeak", "run", str(case["path"]),
                        "--candidate", "scenario:filesystem", "--key", case["id"], "--db", str(root / "runs.sqlite3"),
                        "--output", str(output)], env=env, text=True, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 1 if expected == "fail" else 0, result.stderr)
                    report = json.loads(output.read_text())
                    self.assertEqual(report["candidates"][0]["verdict"], expected)
                    self.assertEqual(report["candidates"][0]["cleanup_status"], "cleaned")
                    self.assertEqual(fingerprint(report["candidates"][0]["evidence"]), report["candidates"][0]["evidence_hash"])
                    self.assertEqual(fingerprint(json.loads(case["path"].read_text())), case["test_hash"])
            self.assertEqual(list((root / "objects").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
