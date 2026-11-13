"""One workflow language, many user-authored acceptance questions, actual observations."""

import copy
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.scenario import JSONReadConnector, ScenarioAdapter
from proofbeak.engine import run_trial
from proofbeak.executor import SubprocessExecutor
from proofbeak.lifecycle import redact_report
from proofbeak.models import fingerprint
from proofbeak.packs import builtins
from proofbeak.packs.scenario import ScenarioPack, conditions
from proofbeak.report import render_html
from proofbeak.service import ServiceError, Tenant, TrialService
from proofbeak.store import IdempotencyConflict, Store
from proofbeak.worker_adapters import decode_adapter, encode_adapter


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.pack = ScenarioPack()
        self.spec = self.pack.example()
        self.adapter = ScenarioAdapter(FilesystemAdapter(self.root / "objects"))
        self.store = Store(self.root / "ledger.sqlite3")
        self.addCleanup(self.store.close)

    def run_spec(self, spec=None, key="test", adapter=None):
        return run_trial(spec or self.spec, [adapter or self.adapter], self.store, key)["candidates"][0]

    def test_delete_preserve_and_update_are_definitions_for_the_same_package(self):
        deleted = self.run_spec(key="delete")
        self.assertEqual(deleted["verdict"], "pass")
        changed = copy.deepcopy(self.spec)
        changed["name"] = "Replace content and preserve the other object"
        changed["fixture"]["replacement"] = "new synthetic contents"
        changed["steps"] = [{"id": "replace", "op": "object.put", "args": {
            "key": {"ref": ["fixture", "key"]}, "content": {"ref": ["fixture", "replacement"]}}}]
        changed["expect"] = [
            {"name": "new_contents", "actual": ["after", "target", "content"], "op": "eq",
             "value": {"ref": ["fixture", "replacement"]}},
            {"name": "both_remain", "actual": ["after", "listing", "count"], "op": "eq", "value": 2},
            self.spec["expect"][-1]]
        updated = self.run_spec(changed, key="update")
        self.assertEqual(updated["verdict"], "pass")
        self.assertEqual(updated["pack_id"], deleted["pack_id"])
        self.assertEqual(updated["evidence"]["after"]["values"]["target"]["content"], changed["fixture"]["replacement"])
        self.assertNotEqual(updated["test_hash"], deleted["test_hash"])
        self.assertEqual(list(self.adapter.backend.root.iterdir()), [])

    def test_user_expectations_determine_verdict_not_a_baked_in_case(self):
        self.spec["expect"][1]["value"] = 7
        row = self.run_spec()
        self.assertEqual(row["verdict"], "fail")
        self.assertEqual(row["checks"][1]["expected"], 7)
        self.assertEqual(row["checks"][1]["observed"], 1)
        self.assertEqual(row["cleanup_status"], "cleaned")

    def test_an_acknowledged_write_is_not_an_observed_success(self):
        original = self.adapter._object
        def dropped_delete(resource, operation, arguments):
            return {"accepted": True} if operation == "object.delete" else original(resource, operation, arguments)
        with patch.object(self.adapter, "_object", side_effect=dropped_delete):
            row = self.run_spec()
        self.assertEqual(row["verdict"], "fail")
        self.assertEqual(row["evidence"]["after"]["values"]["target"]["exists"], True)

    def test_unavailable_observation_cannot_pass_ne_or_equality_to_null(self):
        self.spec["expect"] = [{"name": "missing", "actual": ["after", "target", "content"],
                                "op": "ne", "value": "anything"}]
        self.assertEqual(self.run_spec(key="ne")["verdict"], "inconclusive")
        self.spec["expect"][0].update(op="eq", value=None)
        self.assertEqual(self.run_spec(key="null")["verdict"], "inconclusive")

    def test_failed_baseline_does_not_execute_user_actions(self):
        self.spec["require"][0]["value"] = 9
        row = self.run_spec()
        self.assertEqual(row["verdict"], "inconclusive")
        self.assertEqual(row["reason_code"], "invalid_baseline")
        self.assertFalse(any(event["phase"] == "steps" for event in row["evidence"]["before"]["events"]))
        self.assertEqual(row["evidence"]["before"]["baseline_checks"][0]["observed"], 2)

    def test_report_explains_baseline_failure_and_missing_data_without_rendering_user_html(self):
        self.spec["name"] = '<script>scenario</script>'
        self.spec["require"][0]["value"] = 9
        report = run_trial(self.spec, [self.adapter], self.store, "report-baseline")
        rendered = render_html(report)
        self.assertIn("Initial requirements", rendered)
        self.assertIn("initial_count", rendered)
        self.assertIn("invalid_baseline", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;scenario", rendered)
        self.spec["require"] = []
        self.spec["expect"] = [{"name": "missing", "actual": ["after", "target", "content"],
                                "op": "ne", "value": "anything"}]
        report = run_trial(self.spec, [self.adapter], self.store, "report-missing")
        self.assertIn("unavailable", render_html(report))
        self.assertIn("inconclusive", render_html(redact_report(report)))
        self.assertNotIn("&lt;script&gt;scenario", render_html(redact_report(report)))

    def test_missing_runtime_reference_stops_steps_and_retains_partial_evidence(self):
        self.spec["steps"] = [
            {"id": "read", "op": "object.get", "args": {"key": {"ref": ["fixture", "key"]}}},
            {"id": "bad", "op": "object.put", "args": {"key": "another.txt", "content": {"ref": ["steps", "read", "missing"]}}},
            self.spec["steps"][0]]
        row = self.run_spec()
        self.assertEqual(row["verdict"], "inconclusive")
        self.assertEqual(row["reason_code"], "scenario_value_unavailable")
        events = row["evidence"]["after"]["events"]
        self.assertIn("read", [event["id"] for event in events])
        self.assertNotIn("remove", [event["id"] for event in events])
        self.assertEqual(row["evidence_hash"], fingerprint(row["evidence"]))

    def test_injected_operation_error_is_not_a_provider_failure_and_stops_later_writes(self):
        original = self.adapter._object
        from proofbeak.adapters.base import ProviderError
        def interrupted(resource, operation, arguments):
            if operation == "object.delete":
                raise ProviderError("write_outcome_unknown")
            return original(resource, operation, arguments)
        with patch.object(self.adapter, "_object", side_effect=interrupted):
            row = self.run_spec()
        self.assertEqual(row["verdict"], "inconclusive")
        self.assertEqual(row["reason_code"], "write_outcome_unknown")
        self.assertEqual(row["cleanup_status"], "cleaned")

    def test_invalid_steps_observations_references_and_limits_create_no_resources(self):
        variants = []
        for change in (lambda x: x["steps"][0].update(op="os.system"),
                       lambda x: x["observe"][0].update(op="object.put", args={"key": "x", "content": "x"}),
                       lambda x: x["steps"][0]["args"].update(key="../outside"),
                       lambda x: x["steps"][0]["args"].update(key={"ref": ["env", "HOME"]}),
                       lambda x: x["steps"][0]["args"].update(key={"ref": ["steps", "later", "key"]}),
                       lambda x: x["expect"][0].update(actual=["steps", "remove", "accepted"]),
                       lambda x: x.update(steps=x["steps"] * 17),
                       lambda x: x.update(expect=[]), lambda x: x.update(observe=[]),
                       lambda x: x["steps"][0].update(command="shell")):
            spec = copy.deepcopy(self.spec)
            change(spec)
            variants.append(spec)
        for index, spec in enumerate(variants):
            with self.subTest(index=index), self.assertRaises((ValueError, TypeError)):
                self.run_spec(spec, key=str(index))
        self.assertFalse(self.adapter.backend.root.exists())
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)

    def test_changed_definition_conflicts_but_completed_retry_does_not_execute_again(self):
        first = self.run_spec()
        with patch.object(self.adapter, "provision", side_effect=AssertionError("retry wrote")):
            self.assertEqual(self.run_spec(), first)
        self.spec["expect"][1]["value"] = 2
        with self.assertRaises(IdempotencyConflict):
            self.run_spec()

    def test_real_subprocess_and_worker_codec(self):
        restored = decode_adapter(encode_adapter(self.adapter))
        self.assertEqual(restored.profile, self.adapter.profile)
        result = SubprocessExecutor().run(self.spec, [restored], self.root / "worker.sqlite3", "worker")
        self.assertIsNone(result.reason)
        self.assertEqual(result.payload["report"]["candidates"][0]["verdict"], "pass")

    def test_observation_budget_exhaustion_stops_the_workflow_without_a_false_pass(self):
        self.spec["fixture"]["content"] = "x" * 4096
        self.spec["steps"] = [{"id": "read_" + str(index), "op": "object.get", "args": {
            "key": {"ref": ["fixture", "key"]}}} for index in range(16)]
        row = self.run_spec()
        self.assertEqual(row["verdict"], "inconclusive")
        self.assertEqual(row["reason_code"], "observation_budget_exceeded")
        self.assertEqual(row["cleanup_status"], "cleaned")

    def test_generic_conditions_are_strict_and_support_comparisons(self):
        for operator, value, actual, expected_verdict in (("eq", True, 1, "fail"), ("ne", False, 0, "pass"),
                ("contains", True, [1], "fail"), ("length_eq", 2, [1, 2], "pass"),
                ("gt", 2, 3, "pass"), ("lte", 4, 3, "pass")):
            result = conditions([{"name": "condition", "actual": ["after", "value"], "op": operator, "value": value}],
                                {"after": {"value": actual}}, "actual-read", "now")
            self.assertEqual(result["verdict"], expected_verdict)

    def test_new_case_schema_is_published_and_input_example_is_valid(self):
        catalog = builtins().describe()
        definition = next(item for item in catalog if item["id"] == "scenario.v1")
        self.assertEqual(definition["input_schema"]["properties"]["id"]["const"], "scenario.v1")
        self.assertEqual(self.pack.parse(definition["example_test"]).name, self.spec["name"])

    def test_unknown_connector_capability_is_rejected_before_reservation(self):
        self.spec["observe"][0] = {"id": "json", "op": "http.get", "args": {"route": "item", "params": {}}}
        self.spec["expect"][0]["actual"] = ["after", "json", "json"]
        with self.assertRaises(ValueError):
            self.run_spec()
        self.assertFalse(self.adapter.backend.root.exists())


class ScenarioServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        tenant = Tenant("a", hashlib.sha256(b"a" * 40).hexdigest(), {
            "scenario:filesystem": lambda: ScenarioAdapter(FilesystemAdapter(self.root / "objects"))})
        self.service = TrialService(self.root / "state", [tenant])
        self.service.start(background=False)
        self.addCleanup(self.service.close)
        self.spec = ScenarioPack().example()

    def test_validation_is_non_executing_with_useful_authoring_errors(self):
        valid = self.service.validate_trial("a", self.spec, ["scenario:filesystem"])
        self.assertTrue(valid["valid"])
        self.assertFalse(valid["execution_performed"])
        self.assertFalse(valid["provider_access_checked"])
        self.spec["steps"][0]["args"]["key"] = {"ref": ["steps", "later", "key"]}
        invalid = self.service.validate_trial("a", self.spec, ["scenario:filesystem"])
        self.assertFalse(invalid["valid"])
        self.assertIn("reference", invalid["issues"][0]["message"])
        self.assertEqual(self.service.history("a")["runs"], [])
        self.assertFalse((self.root / "objects").exists())
        with self.assertRaises(ServiceError):
            self.service.validate_trial("a", self.spec, ["scenario:unallowed"])

    def test_queue_evidence_reuse_and_revocation_work_with_custom_scenarios(self):
        run_id = self.service.submit("a", self.spec, ["scenario:filesystem"], "custom")["run_id"]
        self.service.process_next()
        self.assertEqual(self.service.get_run("a", run_id)["candidates"][0]["verdict"], "pass")
        self.assertTrue(self.service.lookup("a", self.spec, ["scenario:filesystem"])["reusable"])
        self.service.revoke("a", run_id)
        self.assertFalse(self.service.lookup("a", self.spec, ["scenario:filesystem"])["reusable"])

    def test_retention_removes_raw_assertion_values_from_both_database_copies(self):
        canary = "scenario-raw-assertion-canary"
        self.spec["setup"][1]["args"]["content"] = canary
        run_id = self.service.submit("a", self.spec, ["scenario:filesystem"], "retention")["run_id"]
        self.service.process_next()
        self.assertIn(canary, json.dumps(self.service.comparison("a", run_id)))
        self.service.maintain(datetime.now(UTC) + timedelta(days=8))
        self.assertNotIn(canary.encode(), self.service.database.read_bytes())
        self.assertNotIn(canary.encode(), (self.service.root / "ledgers" / (run_id + ".sqlite3")).read_bytes())
        self.assertEqual(self.service.comparison("a", run_id)["candidates"][0]["verdict"], "pass")


class HTTPProfileTests(unittest.TestCase):
    def test_http_parameters_cannot_expand_into_another_path_segment(self):
        for value in (".", "..", "../admin", "a/b", "a\\b", "%2e%2e", "a%252fb", "a\u0000b"):
            spec = {"id": "scenario.v1", "name": "A configured read", "fixture": {"key": value},
                "setup": [], "steps": [], "require": [], "observe": [
                    {"id": "item", "op": "http.get", "args": {"route": "item",
                     "params": {"key": {"ref": ["fixture", "key"]}}}}],
                "expect": [{"name": "ready", "actual": ["after", "item", "json", "ready"],
                            "op": "eq", "value": True}]}
            with self.subTest(value=value), self.assertRaises(ValueError):
                ScenarioPack().parse(spec)

    def test_response_mapping_preserves_candidate_identity_across_worker_codec(self):
        adapter = ScenarioAdapter(JSONReadConnector("http:catalog", "http://127.0.0.1:8803", {
            "record": {"path": "/items/{key}", "project": {"ready": ["payload", "enabled"]}}}))
        self.assertEqual(adapter.name, "scenario:http:catalog")
        decoded = decode_adapter(encode_adapter(adapter))
        self.assertEqual(decoded.name, adapter.name)
        self.assertEqual(decoded.profile, adapter.profile)
        self.assertEqual(decoded.describe_operations()[0]["routes"][0]["output_fields"], ["ready"])

    def test_configured_routes_reject_arbitrary_origins_and_path_escape(self):
        for routes in ({"x": "https://foreign.example"}, {"x": "//foreign.example/x"},
                       {"x": "/../x"}, {"x": "/x?token=secret"}, {"x": "/x/{unknown.expression}"}):
            with self.subTest(routes=routes), self.assertRaises(ValueError):
                JSONReadConnector("http:test", "http://127.0.0.1:8080", routes)
        for endpoint in ("http://remote.example", "https://user:password@example.com", "https://example.com/path"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                JSONReadConnector("http:test", endpoint, {"item": "/items/{key}"})
