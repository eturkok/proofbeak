import copy
import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.scenario import ScenarioAdapter
from proofbeak.service import ServiceError, Tenant, TrialService
from proofbeak.suites import example_suite


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        tenants = [Tenant(t, hashlib.sha256((t * 40).encode()).hexdigest(), {
            "scenario:filesystem": lambda t=t: ScenarioAdapter(FilesystemAdapter(root / t))}) for t in ("a", "b")]
        self.service = TrialService(root / "state", tenants)
        self.service.start(background=False)
        self.addCleanup(self.service.close)

    def submit(self, name="Acceptance", tenant="a", finish=True, fail=False):
        definition = example_suite()
        definition["name"] = name
        if fail:
            definition["cases"][0]["test"]["expect"][0]["value"] = "deliberately wrong"
        identifier = self.service.submit_suite(tenant, definition, name)["suite_id"]
        if finish:
            while self.service.process_next():
                pass
        return identifier

    def test_server_search_finds_records_beyond_first_page_and_keeps_filters_on_pagination(self):
        first = self.submit("Özel search 100%", finish=False)
        self.service.cancel_suite("a", first)
        for i in range(28):
            identifier = self.submit("New " + str(i), finish=False)
            self.service.cancel_suite("a", identifier)
        self.assertNotIn(first, [s["suite_id"] for s in self.service.list_suites("a")["suites"]])
        found = self.service.search_history("a", q="öZEL search 100%", state="cancelled")
        self.assertEqual([s["suite_id"] for s in found["suites"]], [first])
        page = self.service.search_history("a", q="New", limit=10)
        second = self.service.search_history("a", q="New", limit=10, before=page["next_cursor"])
        self.assertFalse({x["suite_id"] for x in page["suites"]} & {x["suite_id"] for x in second["suites"]})
        self.assertEqual(self.service.search_history("b", q="New")["suites"], [])
        self.assertEqual(self.service.search_history("a", q="%' OR 1=1 --")["suites"], [])

    def test_acceptance_service_and_date_filters_are_separate_from_execution_state(self):
        passed, failed = self.submit("Pass"), self.submit("Fail", fail=True)
        self.assertEqual(self.service.search_history("a", outcome="pass")["suites"][0]["suite_id"], passed)
        self.assertEqual(self.service.search_history("a", outcome="fail")["suites"][0]["suite_id"], failed)
        self.assertEqual(len(self.service.search_history("a", kind="trial", outcome="fail")["runs"]), 3)
        self.assertEqual(len(self.service.search_history("a", provider="scenario:filesystem")["suites"]), 2)
        self.assertEqual(self.service.search_history("a", provider="other")["suites"], [])
        tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
        self.assertEqual(self.service.search_history("a", since=tomorrow)["suites"], [])
        for kwargs in ({"since": "bad"}, {"state": "pass"}, {"outcome": "completed"}, {"limit": True},
                       {"since": "2026-11-15", "until": "2026-11-14"}):
            with self.assertRaises(ServiceError):
                self.service.search_history("a", **kwargs)

    def test_progress_has_stable_revision_no_payload_or_comparison_and_no_replay(self):
        identifier = self.submit("Progress", finish=False)
        with patch.object(self.service, "comparison", side_effect=AssertionError("must not load evidence")):
            before = self.service.workspace_progress("a", identifier)
            self.assertEqual(before, self.service.workspace_progress("a", identifier))
            self.assertNotIn("definition", before)
            self.assertNotIn("report", json.dumps(before))
            self.service.process_next()
            after = self.service.workspace_progress("a", identifier)
            self.assertNotEqual(before["revision"], after["revision"])
            self.assertEqual(after["completed"], 1)
        with self.assertRaises(ServiceError):
            self.service.workspace_progress("b", identifier)

    def test_templates_are_explicit_private_persistent_and_create_no_job(self):
        definition = example_suite()
        item = self.service.save_workspace_item("a", "template", "Reusable", definition)
        self.assertEqual(self.service.list_suites("a")["suites"], [])
        self.assertEqual(self.service.workspace_item("a", item["id"])["definition"], definition)
        with self.assertRaises(ServiceError):
            self.service.workspace_item("b", item["id"])
        with self.assertRaises(ServiceError):
            self.service.workspace_item("b", item["id"], delete=True)
        with self.assertRaises(ServiceError) as conflict:
            self.service.save_workspace_item("a", "template", "Reusable", definition)
        self.assertEqual(conflict.exception.code, "workspace_name_exists")
        self.service.close()
        self.service.start(background=False)
        self.assertEqual(self.service.list_workspace_items("a", "template")["items"][0]["name"], "Reusable")
        self.service.workspace_item("a", item["id"], delete=True)
        self.assertEqual(self.service.list_workspace_items("a", "template")["items"], [])

    def test_invalid_template_and_nonpassing_baseline_cannot_be_saved(self):
        bad = copy.deepcopy(example_suite())
        bad["cases"][0]["candidates"] = ["foreign"]
        with self.assertRaises(ServiceError):
            self.service.save_workspace_item("a", "template", "Invalid", bad)
        failed = self.submit("Failed baseline", fail=True)
        with self.assertRaises(ServiceError) as error:
            self.service.save_workspace_item("a", "baseline", "Bad", source_id=failed)
        self.assertEqual(error.exception.code, "baseline_requires_passing_evidence")
        identifier = self.submit("Good baseline")
        item = self.service.save_workspace_item("a", "baseline", "Release reference", source_id=identifier)
        self.assertIsNone(self.service.workspace_item("a", item["id"])["definition"])
        self.assertEqual(self.service.list_workspace_items("a", "baseline")["items"][0]["source_state"], "completed")
        self.service.maintain(datetime.now(UTC) + timedelta(days=31))
        self.assertIsNone(self.service.list_workspace_items("a", "baseline")["items"][0]["source_state"])

    def test_operational_metrics_are_tenant_scoped_and_do_not_export_payloads(self):
        self.submit("Secret fixture name", tenant="a")
        self.submit("Foreign", tenant="b")
        metrics = self.service.workspace_metrics("a")
        self.assertEqual(metrics["jobs"]["completed"], 3)
        self.assertEqual(metrics["sample_size"], 3)
        self.assertGreater(metrics["retained_report_bytes"], 0)
        self.assertEqual(metrics["cleanup_pending_runs"], 0)
        self.assertNotIn("Secret fixture name", json.dumps(metrics))
        self.assertNotIn("Foreign", json.dumps(metrics))
        self.assertIsNotNone(metrics["p95_queue_and_run_seconds"])
        with self.service._db() as db:
            identifier = db.execute("SELECT id FROM jobs WHERE tenant='a' LIMIT 1").fetchone()[0]
            db.execute("UPDATE jobs SET state='interrupted',reason='trial_timed_out',result=NULL WHERE id=?", (identifier,))
        self.assertEqual(self.service.workspace_metrics("a")["recent_timeout_runs"], 1)

    def test_purge_removes_explicitly_saved_items_only_for_target_tenant(self):
        self.service.save_workspace_item("a", "template", "A", example_suite())
        self.service.save_workspace_item("b", "template", "B", example_suite())
        self.service.purge_tenant("a")
        with self.service._db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM workspace_items WHERE tenant='a'").fetchone()[0], 0)
        self.assertEqual(len(self.service.list_workspace_items("b", "template")["items"]), 1)


if __name__ == "__main__":
    unittest.main()
