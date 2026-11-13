import copy
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree.ElementTree import fromstring

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.scenario import ScenarioAdapter
from proofbeak.cli import main
from proofbeak.ci import gate, junit, markdown, render_suite_html
from proofbeak.models import fingerprint
from proofbeak.regression import compare_reports
from proofbeak.store import IdempotencyConflict, Store
from proofbeak.suites import example_suite, expand_suite, run_suite


class SuiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.definition = example_suite()
        self.store = Store(self.root / "runs.sqlite3")
        self.addCleanup(self.store.close)

    def adapters(self, names):
        return [ScenarioAdapter(FilesystemAdapter(self.root / "objects")) for _ in names]

    def run_suite(self, key="suite"):
        return run_suite(self.definition, self.adapters, self.store, key)

    def test_matrix_is_deterministic_and_does_not_modify_the_input(self):
        original = copy.deepcopy(self.definition)
        plan = expand_suite(self.definition)
        self.assertEqual(len(plan), 3)
        self.assertEqual(plan, expand_suite(json.loads(json.dumps(self.definition))))
        self.assertEqual(self.definition, original)
        self.assertEqual(len({p["id"] for p in plan}), 3)

    def test_real_filesystem_suite_and_idempotent_parent_retrieval(self):
        report = self.run_suite()
        self.assertEqual(report["gate"], "pass")
        self.assertEqual(report["counts"]["pass"], 3)
        self.assertEqual(self.run_suite(), report)
        self.assertEqual(list((self.root / "objects").iterdir()), [])
        self.definition["name"] = "A different suite"
        with self.assertRaises(IdempotencyConflict):
            self.run_suite()

    def test_invalid_last_case_prevents_all_provider_operations(self):
        bad = copy.deepcopy(self.definition["cases"][0])
        bad["id"] = "invalid"
        bad["test"]["steps"][0]["op"] = "unregistered.operation"
        self.definition["cases"].append(bad)
        with self.assertRaises(ValueError):
            self.run_suite()
        self.assertFalse((self.root / "objects").exists())
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)

    def test_explosion_duplicate_dimensions_and_unused_parameters_rejected(self):
        for matrix in ({"content": ["a"] * 2}, {"content": list(range(16)), "other": list(range(16))},
                       {"unused": ["a"]}):
            value = copy.deepcopy(self.definition)
            value["cases"][0]["matrix"] = matrix
            with self.assertRaises(ValueError):
                expand_suite(value)

    def test_ci_exports_preserve_fail_inconclusive_and_cleanup(self):
        report = self.run_suite()
        self.assertEqual(fromstring(junit(report)).get("tests"), "3")
        self.assertEqual(fromstring(junit(report)).get("failures"), "0")
        self.assertIn("Gate: pass", markdown(report))
        candidate = report["entries"][0]["report"]["candidates"][0]
        candidate["verdict"] = "fail"
        self.assertEqual(gate(report)["gate"], "fail")
        self.assertEqual(fromstring(junit(report)).get("failures"), "1")
        candidate["verdict"] = "inconclusive"
        self.assertEqual(gate(report)["gate"], "inconclusive")
        candidate["verdict"] = "pass"
        candidate["cleanup_status"] = "quarantined"
        self.assertEqual(gate(report)["gate"], "inconclusive")
        self.assertEqual(fromstring(junit(report)).get("errors"), "1")
        candidate["cleanup_status"] = "cleaned"
        candidate["evidence_level"] = "simulation"
        self.assertEqual(gate(report)["gate"], "inconclusive")

    def test_comparison_reports_regressions_and_refuses_changed_tests(self):
        baseline = self.run_suite()
        current = copy.deepcopy(baseline)
        candidate = current["entries"][0]["report"]["candidates"][0]
        candidate["checks"][0]["passed"] = False
        candidate["checks"][0]["observed"] = "changed"
        result = compare_reports(baseline, current)
        self.assertTrue(result["comparable"])
        self.assertEqual(result["regressions"], 1)
        self.assertEqual(result["gate"], "fail")
        candidate["test_hash"] = "0" * 64
        self.assertFalse(compare_reports(baseline, current)["comparable"])

    def test_version_comparison_requires_explicit_acknowledgment_and_keeps_other_context(self):
        baseline = self.run_suite()
        current = copy.deepcopy(baseline)
        candidate = current["entries"][0]["report"]["candidates"][0]
        candidate["profile"]["provider_version"] = "2.0"
        self.assertFalse(compare_reports(baseline, current)["comparable"])
        self.assertTrue(compare_reports(baseline, current, True)["comparable"])
        candidate["profile"]["endpoint"] = "https://different.example"
        self.assertFalse(compare_reports(baseline, current, True)["comparable"])

    def test_corrupt_observations_missing_members_and_changed_expectations_cannot_pass_diff(self):
        baseline = self.run_suite()
        for mutation in ("evidence", "missing", "expected", "lost"):
            current = copy.deepcopy(baseline)
            candidate = current["entries"][0]["report"]["candidates"][0]
            if mutation == "evidence":
                candidate["evidence"]["after"] = {}
            elif mutation == "missing":
                current["entries"].pop()
            elif mutation == "expected":
                candidate["checks"][0]["expected"] = "different"
            else:
                candidate["checks"][0]["available"] = False
            self.assertEqual(compare_reports(baseline, current)["gate"], "inconclusive", mutation)

    def test_report_escapes_user_supplied_names(self):
        report = self.run_suite()
        report["name"] = '<img src=x onerror="alert(1)">'
        rendered = render_suite_html(report)
        self.assertNotIn('<img src=x', rendered)
        self.assertIn('&lt;img', rendered)

    def test_cli_export_cannot_replace_the_suite_or_operator_profile(self):
        path = self.root / "suite.json"
        path.write_text(json.dumps(self.definition))
        original = path.read_bytes()
        self.assertEqual(main(["suite", "run", str(path), "--key", "x", "--output", str(path)]), 3)
        self.assertEqual(path.read_bytes(), original)

    def test_changed_observation_with_same_pass_is_a_change_not_a_regression(self):
        report = self.run_suite()
        current = copy.deepcopy(report)
        candidate = current["entries"][0]["report"]["candidates"][0]
        candidate["checks"][0]["observed"] = {"alternative": True}
        result = compare_reports(report, current)
        self.assertEqual(result["gate"], "pass")
        self.assertTrue(any(c["status"] == "changed" for e in result["entries"] for c in e["changes"]))
        self.assertEqual(fingerprint(report), fingerprint(self.run_suite()))
