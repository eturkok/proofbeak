"""A caller can locate, repair and run a definition without speculative provider work."""

import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.scenario import JSONReadConnector, ScenarioAdapter
from proofbeak.cli import main
from proofbeak.packs import builtins
from proofbeak.packs.scenario import ScenarioPack
from proofbeak.service import ServiceError, Tenant, TrialService
from proofbeak.validation import TrialValidationError, candidate_issues


class AuthoringTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.spec = ScenarioPack().example()

    def cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def service(self, providers):
        tenant = Tenant("a", hashlib.sha256(b"a" * 40).hexdigest(), providers)
        service = TrialService(self.root / "state", [tenant])
        service.start(background=False)
        self.addCleanup(service.close)
        return service

    def test_use_reported_location_to_repair_definition_then_run_the_same_key(self):
        service = self.service({"scenario:filesystem": lambda: ScenarioAdapter(FilesystemAdapter(self.root / "objects"))})
        self.spec["steps"][0]["args"]["key"] = {"ref": ["steps", "later", "key"]}
        checked = service.validate_trial("a", self.spec, ["scenario:filesystem"])
        self.assertFalse(checked["valid"])
        self.assertFalse(checked["execution_performed"])
        self.assertFalse(checked["provider_access_checked"])
        issue = checked["issues"][0]
        self.assertEqual(issue["code"], "invalid_reference")
        self.assertEqual(issue["path"], ["steps", 0, "args", "key", "ref"])
        current = self.spec
        for part in issue["path"][:-1]:
            current = current[part]
        current[issue["path"][-1]] = ["fixture", "key"]
        self.assertTrue(service.validate_trial("a", self.spec, ["scenario:filesystem"])["valid"])
        self.assertEqual(service.history("a")["runs"], [])
        self.assertFalse((self.root / "objects").exists())
        run = service.submit("a", self.spec, ["scenario:filesystem"], "caller-key")
        service.process_next()
        self.assertEqual(service.get_run("a", run["run_id"])["candidates"][0]["verdict"], "pass")
        self.assertEqual(list((self.root / "objects").iterdir()), [])

    def test_every_incompatible_candidate_is_identified_without_any_provider_request(self):
        providers = {"scenario:http:" + name: lambda name=name: ScenarioAdapter(JSONReadConnector(
            "http:" + name, "http://127.0.0.1:8803", {"item": "/items/{key}"})) for name in ("a", "b")}
        providers["scenario:filesystem"] = lambda: ScenarioAdapter(FilesystemAdapter(self.root / "objects"))
        service = self.service(providers)
        with patch("proofbeak.adapters.http.Transport.request", side_effect=AssertionError("validation contacted provider")):
            checked = service.validate_trial("a", self.spec, list(providers))
        self.assertEqual([issue["candidate"] for issue in checked["issues"]], ["scenario:http:a", "scenario:http:b"])
        self.assertTrue(all(issue["code"] == "unsupported_candidate_operation" for issue in checked["issues"]))
        self.assertTrue(all(issue["path"] == ["setup", 0, "op"] for issue in checked["issues"]))
        self.assertEqual(service.history("a")["runs"], [])
        self.assertFalse((self.root / "objects").exists())
        with self.assertRaises(ServiceError) as denied:
            service.validate_trial("a", {"id": "unknown"}, ["scenario:http:foreign"])
        self.assertEqual(denied.exception.status, 403)

    def test_route_and_parameter_diagnostics_point_to_the_candidate_definition_boundary(self):
        adapter = ScenarioAdapter(JSONReadConnector("http:a", "http://127.0.0.1:8803", {"item": "/items/{key}"}))
        spec = {"id": "scenario.v1", "name": "A caller read", "fixture": {}, "setup": [], "steps": [],
            "observe": [{"id": "item", "op": "http.get", "args": {"route": "unknown", "params": {}}}],
            "require": [], "expect": [{"name": "ready", "actual": ["after", "item", "json", "ready"],
                                      "op": "eq", "value": True}]}
        pack, trial = builtins().prepare(spec)
        issue = candidate_issues(pack, trial, [adapter])[0]
        self.assertEqual((issue["code"], issue["path"]), ("route_not_configured", ["observe", 0, "args", "route"]))
        spec["observe"][0]["args"]["route"] = "item"
        issue = candidate_issues(pack, pack.parse(spec), [adapter])[0]
        self.assertEqual((issue["code"], issue["path"]), ("route_parameters_mismatch", ["observe", 0, "args", "params"]))

    def test_definition_diagnostics_do_not_echo_argument_values(self):
        canary = "private-input-value-do-not-echo"
        variants = [
            (["steps", 0, "op"], canary, "unsupported_operation", ["steps", 0, "op"]),
            (["steps", 0, "args", "key"], "../" + canary, "invalid_object_key", ["steps", 0, "args", "key"]),
            (["expect", 0, "actual"], ["steps", "remove", "accepted"], "invalid_observation_reference", ["expect", 0, "actual"]),
            (["setup", 0, "args", "content"], {"ref": ["fixture", canary]}, "invalid_reference", ["setup", 0, "args", "content", "ref"]),
        ]
        for location, value, code, expected_path in variants:
            spec = copy.deepcopy(self.spec)
            current = spec
            for part in location[:-1]:
                current = current[part]
            current[location[-1]] = value
            with self.subTest(location=location), self.assertRaises(TrialValidationError) as error:
                ScenarioPack().parse(spec)
            self.assertEqual(error.exception.issue["code"], code)
            self.assertEqual(error.exception.issue["path"], expected_path)
            self.assertNotIn(canary, json.dumps(error.exception.issue))

    def test_cli_json_and_readable_feedback_and_corrected_definition(self):
        definition = self.root / "mine.json"
        self.spec["steps"][0]["args"]["key"] = {"ref": ["steps", "later", "key"]}
        definition.write_text(json.dumps(self.spec))
        code, stdout, stderr = self.cli("validate", str(definition), "--json")
        self.assertEqual(code, 3)
        self.assertEqual(stderr, "")
        checked = json.loads(stdout)
        self.assertEqual(checked["issues"][0]["path"], ["steps", 0, "args", "key", "ref"])
        code, stdout, stderr = self.cli("validate", str(definition))
        self.assertEqual(code, 3)
        self.assertIn("/steps/0/args/key/ref", stderr)
        self.spec["steps"][0]["args"]["key"] = {"ref": ["fixture", "key"]}
        definition.write_text(json.dumps(self.spec))
        with patch.dict("os.environ", {"PROOFBEAK_FILESYSTEM_ROOT": str(self.root / "objects")}):
            code, stdout, _ = self.cli("validate", str(definition), "--candidate", "scenario:filesystem", "--json")
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(stdout)["valid"])
            self.assertFalse((self.root / "objects").exists())
            code, _, stderr = self.cli("run", str(definition), "--candidate", "scenario:filesystem", "--key", "my-key",
                "--db", str(self.root / "runs.sqlite3"), "--output", str(self.root / "report.json"))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads((self.root / "report.json").read_text())["candidates"][0]["verdict"], "pass")

    def test_cli_json_syntax_error_and_candidate_budget_remain_non_executing(self):
        definition = self.root / "broken.json"
        definition.write_text('{"id":')
        code, stdout, stderr = self.cli("validate", str(definition), "--json")
        self.assertEqual(code, 3)
        self.assertEqual(stderr, "")
        self.assertFalse(json.loads(stdout)["valid"])
        definition.write_text('[' * 1500 + '0' + ']' * 1500)
        code, stdout, stderr = self.cli("validate", str(definition), "--json")
        self.assertEqual(code, 3)
        self.assertFalse(json.loads(stdout)["valid"])
        self.assertEqual(stderr, "")
        definition.write_text(json.dumps(self.spec))
        code, stdout, _ = self.cli("validate", str(definition), "--json", "--candidate", "scenario:filesystem",
                                  "--candidate", "scenario:filesystem")
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(stdout)["issues"][0]["code"], "invalid_candidates")
        self.assertEqual(list(self.root.iterdir()), [definition])

    def test_invalid_parameter_names_cannot_expand_diagnostic_payloads(self):
        self.spec.update(setup=[], steps=[], require=[])
        self.spec["observe"] = [{"id": "item", "op": "http.get", "args": {
            "route": "item", "params": {"x" * 20000: {"ref": ["fixture", "missing"]}}}}]
        with self.assertRaises(TrialValidationError) as error:
            ScenarioPack().parse(self.spec)
        self.assertEqual(error.exception.issue["path"], ["observe", 0, "args", "params"])
        self.assertLess(len(json.dumps(error.exception.issue)), 400)

    def test_readable_locations_do_not_execute_control_characters_from_json_keys(self):
        self.spec["fixture"] = {"\u001b[2J": "x" * 4097}
        definition = self.root / "control-key.json"
        definition.write_text(json.dumps(self.spec))
        code, _, stderr = self.cli("validate", str(definition))
        self.assertEqual(code, 3)
        self.assertNotIn("\u001b", stderr)
        self.assertIn("/fixture/\\u001b[2J", stderr)


if __name__ == "__main__":
    unittest.main()
