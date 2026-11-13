import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proofbeak.adapters.base import ProviderError
from proofbeak.adapters.http import Transport
from proofbeak.adapters.simulation import SimulationAdapter
from proofbeak.engine import run_trial
from proofbeak.report import render_html
from proofbeak.store import Store


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "runs.sqlite3")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.store.close)
        self.spec = json.loads(Path("examples/calendar.move_one.v1.json").read_text())

    def test_measurements_include_cleanup_and_do_not_refresh_on_retry(self):
        adapter = SimulationAdapter()
        adapter.transport = Transport()
        original_cleanup = adapter.cleanup
        def cleanup(resource):
            adapter.transport.calls = 0
            adapter.transport.total_calls += 2
            original_cleanup(resource)
        with patch.object(adapter, "cleanup", side_effect=cleanup), \
                patch("proofbeak.engine.time.monotonic", side_effect=[10, 12.5]), \
                patch("proofbeak.engine.time.process_time", side_effect=[2, 2.25]):
            report = run_trial(self.spec, [adapter], self.store, "metrics")
        measured = report["candidates"][0]["measurements"]
        self.assertEqual(measured["wall_seconds"], 2.5)
        self.assertEqual(measured["cpu_seconds"], .25)
        self.assertEqual(measured["provider_http_attempts"], 2)
        self.assertIsNone(measured["llm_cost_usd"])
        self.assertEqual(run_trial(self.spec, [adapter], self.store, "metrics"), report)

    def test_unknown_counter_is_null_and_failed_cleanup_still_measured(self):
        with patch.object(SimulationAdapter, "cleanup", side_effect=ProviderError("permission_denied")):
            report = run_trial(self.spec, [SimulationAdapter()], self.store, "quarantine")
        candidate = report["candidates"][0]
        self.assertEqual(candidate["verdict"], "pass")
        self.assertEqual(candidate["cleanup_status"], "quarantined")
        self.assertIsNone(candidate["measurements"]["provider_http_attempts"])
        self.assertGreaterEqual(candidate["measurements"]["wall_seconds"], 0)
        self.assertIn("External agent tokens and cost: unmeasured", render_html(report))


if __name__ == "__main__":
    unittest.main()
