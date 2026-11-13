"""Scorer regression fixtures are synthetic studies, never evidence of an actual pilot."""

import copy
import hashlib
import json
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.http import Transport
from proofbeak.adapters.radicale import RadicaleAdapter
from proofbeak.engine import run_trial
from proofbeak.models import fingerprint
from proofbeak.pilot import evaluate, initialize, read_json, render_html, seal_reference
from proofbeak.store import Store
from proofbeak.worker_adapters import encode_adapter

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)
SEALED = NOW - timedelta(days=20)


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ref_path = self.root / "reference.json"
        self.seal_path = self.root / "reference.seal.json"
        self.answers_path = self.root / "answers.json"
        self.adoption_path = self.root / "adoption.json"
        settings = {"model": "test-model-version", "instructions_sha256": "a"*64,
                    "repeats": 2, "token_budget": 100, "wall_budget_seconds": 30}
        self.ref = {"schema_version": "1", "study_id": "test-only", "protocol": settings, "cases": []}
        self.answers = {"schema_version": "1", "study_id": "test-only", "reference_sha256": None,
                        "protocol": settings, "answers": []}
        with closing(Store(self.root / "trials.sqlite3")) as store:
            adapter = FilesystemAdapter(self.root / "objects")
            for index in range(12):
                case_id = f"case-{index}"
                spec = json.loads(Path("examples/storage.put_same_key.v1.json").read_text())
                spec["fixture"]["content"] = f"Synthetic scorer fixture {index}"
                case = {"id": case_id, "question": f"Synthetic question {index}",
                        "test_sha256": fingerprint(spec), "docs_sha256": "b"*64,
                        "candidates": ["filesystem", "other"], "reviews": [], "evidence": []}
                for reviewer in ("reviewer-1", "reviewer-2"):
                    case["reviews"].append({"reviewer_id": reviewer,
                        "recorded_at": (SEALED - timedelta(hours=1)).isoformat(),
                        "judgments": {"filesystem": "pass", "other": "fail"}})
                if index < 4:
                    report = run_trial(spec, [adapter], store, case_id)
                    path = self.root / f"trial-{index}.json"
                    path.write_text(json.dumps(report))
                    case["evidence"].append({"path": path.name,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "provider": "filesystem"})
                self.ref["cases"].append(case)
                for arm in ("A", "B"):
                    for repeat in (1, 2):
                        self.answers["answers"].append({"case_id": case_id, "arm": arm, "repeat": repeat,
                            "attempt_id": f"{case_id}-{arm}-{repeat}", "response_sha256": "c"*64,
                            "finished_at": (SEALED + timedelta(hours=2)).isoformat(),
                            "decision": "other" if arm == "A" and index < 2 else "filesystem",
                            "wall_seconds": 2, "tokens": 20, "cost_usd": None})
        developers = []
        for index in range(3):
            developers.append({"developer_id": f"dev-{index}", "started_at": SEALED.isoformat(),
                "first_report_at": (SEALED + timedelta(minutes=15)).isoformat(),
                "first_test_sha256": "d"*64, "return_at": (SEALED + timedelta(days=2)).isoformat(),
                "return_test_sha256": "e"*64})
        self.adoption = {"schema_version": "1", "study_id": "test-only", "developers": developers}
        self.seal()

    def seal(self):
        self.ref_path.write_text(json.dumps(self.ref))
        self.seal_path.unlink(missing_ok=True)
        seal = seal_reference(self.ref_path, self.seal_path, now=SEALED)
        self.answers["reference_sha256"] = seal["reference_sha256"]

    def score(self):
        self.answers_path.write_text(json.dumps(self.answers))
        self.adoption_path.write_text(json.dumps(self.adoption))
        return evaluate(self.ref_path, self.seal_path, self.answers_path, self.adoption_path, now=NOW)

    def rows(self, arm, case="case-0"):
        return [r for r in self.answers["answers"] if r["arm"] == arm and r["case_id"] == case]

    def test_complete_test_fixture_scores_exact_pilot_gates(self):
        report = self.score()
        self.assertEqual(report["decision"], "continue")
        self.assertEqual(report["correct_decision_gain"], 2)
        self.assertEqual(report["distinct_real_observations"], 4)
        self.assertEqual(report["arms"]["A"]["correct_cases"], 10)
        self.assertEqual(report["arms"]["A"]["false_positive_attempts"], 4)
        self.assertIsNone(report["arms"]["B"]["cost_usd"])

    def test_unstable_case_is_excluded_but_false_positive_is_still_reported(self):
        self.rows("B")[0]["decision"] = "other"
        report = self.score()
        self.assertEqual(report["decision"], "revise")
        self.assertEqual(len(report["valid_cases"]), 11)
        self.assertIn("unstable_B", report["excluded_cases"][0]["reasons"])
        self.assertEqual(report["arms"]["B"]["false_positive_attempts"], 1)

    def test_missing_and_unmeasured_answers_never_imply_success_or_zero_cost(self):
        self.answers["answers"] = []
        report = self.score()
        self.assertEqual(report["decision"], "insufficient_data")
        self.assertEqual(report["gates"]["zero_false_positives_B"], "pending")
        self.assertIsNone(report["arms"]["B"]["tokens"])
        self.assertIsNone(report["arms"]["B"]["wall_seconds"])
        self.assertIsNone(report["arms"]["B"]["cost_usd"])

    def test_missing_budget_is_excluded_and_budget_overrun_is_separate(self):
        self.rows("B")[0]["tokens"] = None
        self.rows("A", "case-1")[0]["wall_seconds"] = 31
        report = self.score()
        reasons = {reason for item in report["excluded_cases"] for reason in item["reasons"]}
        self.assertEqual(len(report["valid_cases"]), 10)
        self.assertIn("budget_unmeasured", reasons)
        self.assertIn("budget_exceeded", reasons)
        self.assertIsNone(report["arms"]["B"]["tokens"])

    def test_review_disagreement_is_not_scored_as_fail(self):
        self.ref["cases"][0]["reviews"][1]["judgments"]["other"] = "inconclusive"
        self.seal()
        report = self.score()
        self.assertIn("reviewer_disagreement", report["excluded_cases"][0]["reasons"])
        self.assertEqual(report["decision"], "insufficient_data")

    def test_inconclusive_abstention_is_distinct_from_no_match(self):
        case = self.ref["cases"][11]
        for review in case["reviews"]:
            review["judgments"] = {"filesystem": "inconclusive", "other": "fail"}
        for arm in ("A", "B"):
            for row in self.rows(arm, "case-11"):
                row["decision"] = "insufficient_evidence"
        self.seal()
        report = self.score()
        self.assertEqual(report["decision"], "continue")
        self.assertEqual(report["valid_cases"][-1]["acceptable_decisions"], ["insufficient_evidence"])
        for row in self.rows("B", "case-11"):
            row["decision"] = "no_match"
        self.assertEqual(self.score()["arms"]["B"]["correct_cases"], 11)

    def test_unsupported_selection_cannot_hide_as_non_false_positive(self):
        for review in self.ref["cases"][11]["reviews"]:
            review["judgments"] = {"filesystem": "inconclusive", "other": "fail"}
        self.seal()
        report = self.score()
        self.assertEqual(report["arms"]["B"]["false_positive_attempts"], 0)
        self.assertEqual(report["arms"]["B"]["unsupported_selection_attempts"], 2)
        self.assertEqual(report["gates"]["zero_unsupported_selections_B"], "fail")

    def test_known_failed_candidate_cannot_be_fixed_by_retroactive_reference_change(self):
        self.ref["cases"][11]["question"] = "changed after agent answer"
        self.ref_path.write_text(json.dumps(self.ref))
        with self.assertRaisesRegex(ValueError, "changed after sealing"):
            self.score()

    def test_answers_before_sealing_and_changed_model_are_rejected(self):
        self.rows("B")[0]["finished_at"] = (SEALED - timedelta(minutes=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "after the reference seal"):
            self.score()
        self.rows("B")[0]["finished_at"] = NOW.isoformat()
        self.answers["protocol"] = copy.deepcopy(self.ref["protocol"])
        self.answers["protocol"]["model"] = "different-model"
        with self.assertRaisesRegex(ValueError, "protocol"):
            self.score()

    def test_duplicate_attempts_and_identical_cases_are_rejected(self):
        self.answers["answers"].append(copy.deepcopy(self.answers["answers"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate answer"):
            self.score()
        self.ref["cases"][1]["test_sha256"] = self.ref["cases"][0]["test_sha256"]
        with self.assertRaisesRegex(ValueError, "duplicate decision"):
            self.seal()

    def test_two_review_records_must_have_different_ids(self):
        self.ref["cases"][0]["reviews"][1]["reviewer_id"] = "reviewer-1"
        with self.assertRaisesRegex(ValueError, "identifiers must differ"):
            self.seal()

    def test_missing_real_evidence_does_not_pass_continuation(self):
        for case in self.ref["cases"]:
            case["evidence"] = []
        self.seal()
        report = self.score()
        self.assertEqual(report["distinct_real_observations"], 0)
        self.assertEqual(report["gates"]["four_real_observations"], "fail")
        self.assertEqual(report["decision"], "revise")

    def test_simulated_report_never_counts_toward_real_observations(self):
        for case in self.ref["cases"]:
            for item in case["evidence"]:
                path = self.root / item["path"]
                report = json.loads(path.read_text())
                report["candidates"][0]["evidence_level"] = "simulation"
                path.write_text(json.dumps(report))
                item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.seal()
        self.assertEqual(self.score()["distinct_real_observations"], 0)

    def test_evidence_file_and_raw_observation_tampering_are_rejected(self):
        item = self.ref["cases"][0]["evidence"][0]
        path = self.root / item["path"]
        path.write_text(path.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "file digest changed"):
            self.score()
        report = json.loads(path.read_text())
        report["candidates"][0]["evidence"]["after"] = {"changed": True}
        path.write_text(json.dumps(report))
        item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "raw evidence"):
            self.seal()

    def test_evidence_path_must_remain_inside_study(self):
        self.ref["cases"][0]["evidence"][0]["path"] = "../secret.json"
        with self.assertRaisesRegex(ValueError, "inside the study"):
            self.seal()

    def test_return_requires_a_different_condition(self):
        for row in self.adoption["developers"]:
            row["return_test_sha256"] = row["first_test_sha256"]
        report = self.score()
        self.assertEqual(report["adoption"]["new_condition_within_14d"], 0)
        self.assertEqual(report["gates"]["two_return_with_new_condition_within_14d"], "fail")

    def test_incomplete_adoption_window_stays_pending(self):
        for row in self.adoption["developers"]:
            row.update({"started_at": (NOW - timedelta(minutes=5)).isoformat(),
                        "first_report_at": None, "first_test_sha256": None,
                        "return_at": None, "return_test_sha256": None})
        report = self.score()
        self.assertEqual(report["gates"]["two_of_three_first_reports_within_30m"], "pending")
        self.assertEqual(report["gates"]["two_return_with_new_condition_within_14d"], "pending")

    def test_unknown_numbers_and_duplicate_json_keys_are_rejected(self):
        self.rows("B")[0]["tokens"] = float("nan")
        with self.assertRaisesRegex(ValueError, "non-finite"):
            self.score()
        path = self.root / "duplicates.json"
        path.write_text('{"a":1,"a":2}')
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            read_json(path)

    def test_init_never_overwrites_and_empty_templates_cannot_be_sealed(self):
        root = self.root / "new-study"
        initialize(root)
        with self.assertRaisesRegex(ValueError, "overwrites"):
            initialize(root)
        with self.assertRaisesRegex(ValueError, "fill the template"):
            seal_reference(root / "reference.json", root / "reference.seal.json", now=NOW)

    def test_html_escapes_report_content(self):
        output = render_html({"study_id": '<script>alert("x")</script>'})
        self.assertNotIn("<script>", output)
        self.assertIn("&lt;script&gt;", output)

    def test_injected_radicale_transport_is_always_simulation_and_not_serialized_as_real(self):
        adapter = RadicaleAdapter("http://127.0.0.1:5232", "test", "test", transport=Transport())
        self.assertEqual(adapter.evidence_level, "simulation")
        with self.assertRaises(ValueError):
            encode_adapter(adapter)


if __name__ == "__main__":
    unittest.main()
