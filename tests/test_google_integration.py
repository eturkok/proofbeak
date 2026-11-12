"""Explicitly opt-in test that creates and deletes one synthetic Google secondary calendar."""

import json
import os
import unittest
from pathlib import Path
from uuid import uuid4

from proofbeak.adapters.google import GoogleCalendarAdapter
from proofbeak.engine import run_trial
from proofbeak.report import render_html
from proofbeak.store import Store

OPT_IN = os.environ.get("PROOFBEAK_RUN_GOOGLE_LIVE") == "1"


@unittest.skipUnless(OPT_IN, "Google live trial requires explicit opt-in and a dedicated test account")
class GoogleIntegrationTests(unittest.TestCase):
    def test_real_google_recurring_exception_and_cleanup(self):
        adapter = GoogleCalendarAdapter(os.environ.get("PROOFBEAK_GOOGLE_ACCESS_TOKEN", ""),
                                        os.environ.get("PROOFBEAK_GOOGLE_TEST_ACCOUNT", ""))
        spec = json.loads((Path(__file__).resolve().parents[1] /
                           "examples/calendar.move_one.v1.json").read_text())
        # A durable ledger remains available even when the live assertion or cleanup fails.
        store = Store(Path(os.environ.get("PROOFBEAK_GOOGLE_DB", ".proofbeak/google-live.sqlite3")))
        try:
            report = run_trial(spec, [adapter], store, f"google-live-{uuid4().hex}")
            result = report["candidates"][0]
            output = Path(os.environ.get("PROOFBEAK_GOOGLE_REPORT", "reports/google-live.json"))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2) + "\n")
            output.with_suffix(".html").write_text(render_html(report))
            self.assertEqual(result["verdict"], "pass", result["reason_code"])
            self.assertEqual(result["cleanup_status"], "cleaned")
            self.assertEqual(len(result["evidence"]["after"]["events"]), 4)
            self.assertTrue(result["eligible_for_provider_comparison"])
        finally:
            store.close()


if __name__ == "__main__":
    if not OPT_IN:
        raise SystemExit("Set PROOFBEAK_RUN_GOOGLE_LIVE=1 for an explicit disposable-account trial")
    unittest.main()
