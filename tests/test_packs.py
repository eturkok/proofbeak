import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from proofbeak.adapters.base import ProviderError, Resource
from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.simulation import SimulationAdapter
from proofbeak.engine import recover_cleanup, run_trial
from proofbeak.models import fingerprint
from proofbeak.observer import equal, evaluate
from proofbeak.packs import PackRegistry, builtins
from proofbeak.packs.calendar import CalendarPack
from proofbeak.packs.storage import StorageSnapshot
from proofbeak.store import IdempotencyConflict, Store

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class PackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "runs.sqlite3")
        self.addCleanup(self.store.close)
        self.spec = json.loads((EXAMPLES / "storage.put_same_key.v1.json").read_text())
        self.adapter = FilesystemAdapter(self.root / "objects")

    def test_real_filesystem_repeated_write_and_cleanup(self):
        result = run_trial(self.spec, [self.adapter], self.store, "filesystem")
        candidate = result["candidates"][0]
        self.assertEqual(candidate["verdict"], "pass")
        self.assertEqual(candidate["cleanup_status"], "cleaned")
        self.assertEqual(candidate["evidence"]["before"]["objects"],
                         candidate["evidence"]["after"]["objects"])
        self.assertEqual(list(self.adapter.root.iterdir()), [])
        self.assertEqual(candidate["pack_id"], self.spec["id"])
        self.assertEqual(candidate["evidence_hash"], fingerprint(candidate["evidence"]))

    def test_real_filesystem_duplicate_cannot_pass(self):
        def broken_write(resource, trial):
            (Path(resource.id) / "duplicate.txt").write_text(trial.content)
        with patch.object(self.adapter, "apply", side_effect=broken_write):
            result = run_trial(self.spec, [self.adapter], self.store, "duplicate")["candidates"][0]
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["checks"][0]["observed"], 2)

    def test_real_filesystem_changed_content_cannot_pass(self):
        def broken_write(resource, trial):
            (Path(resource.id) / trial.key).write_text("changed")
        with patch.object(self.adapter, "apply", side_effect=broken_write):
            result = run_trial(self.spec, [self.adapter], self.store, "content")["candidates"][0]
        self.assertEqual(result["verdict"], "fail")
        self.assertFalse(result["checks"][2]["passed"])

    def test_cross_domain_adapter_rejected_before_reservation(self):
        with self.assertRaisesRegex(ValueError, "does not support"):
            run_trial(self.spec, [SimulationAdapter()], self.store, "wrong-domain")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)

    def test_packages_listed(self):
        self.assertEqual({p["id"] for p in builtins().describe()},
                         {"calendar.move_one.v1", "storage.put_same_key.v1"})

    def test_unknown_package_cannot_import_code(self):
        self.spec["id"] = "os.system"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            builtins().prepare(self.spec)

    def test_duplicate_package_registration_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            PackRegistry([CalendarPack(), CalendarPack()])

    def test_package_version_change_invalidates_idempotency_context(self):
        pack, _ = builtins().prepare(self.spec)
        run_trial(self.spec, [self.adapter], self.store, "version", PackRegistry([pack]))
        pack.version = "new-observation-semantics"
        with self.assertRaises(IdempotencyConflict):
            run_trial(self.spec, [self.adapter], self.store, "version", PackRegistry([pack]))

    def test_path_traversal_rejected_by_package(self):
        for key in ["../other", "/absolute", ".proofbeak-write", "a/b", ""]:
            self.spec["fixture"]["key"] = key
            with self.assertRaises(ValueError):
                builtins().prepare(self.spec)

    def test_oversized_payload_rejected(self):
        self.spec["fixture"]["content"] = "a" * 4097
        with self.assertRaises(ValueError):
            builtins().prepare(self.spec)

    def test_foreign_resource_cannot_be_cleaned(self):
        with self.assertRaises(ProviderError):
            self.adapter.cleanup(Resource(str(self.root), "a" * 32))

    def test_symlink_resource_cannot_be_cleaned(self):
        self.adapter.root.mkdir()
        resource = self.adapter.resource("a" * 32)
        target = self.root / "foreign"
        target.mkdir()
        Path(resource.id).symlink_to(target, target_is_directory=True)
        with self.assertRaises(ProviderError):
            self.adapter.cleanup(resource)
        self.assertTrue(target.exists())

    def test_legacy_resource_ledger_remains_cleanable(self):
        adapter = SimulationAdapter()
        run_id, _ = self.store.reserve("legacy", "hash")
        resource = adapter.resource(run_id)
        self.store.track(run_id, adapter.name, fingerprint(adapter.profile),
                         {"id": resource.id, "series_id": resource.scope_id})
        self.assertEqual(recover_cleanup(self.store, [adapter])[0]["status"], "cleaned")

    def test_missing_metric_is_inconclusive(self):
        result = evaluate({"success": True}, {}, "provider", "now")
        self.assertEqual(result["verdict"], "inconclusive")

    def test_numeric_boolean_cannot_create_false_positive(self):
        for expected, observed in [(True, 1), ([1], [True]), ({"x": True}, {"x": 1})]:
            self.assertFalse(equal(expected, observed))
        self.assertTrue(equal(60, 60.0))
        self.assertTrue(equal(10 ** 1000, 10 ** 1000))

    def test_nonfinite_metrics_cannot_pass(self):
        self.assertFalse(equal(float("inf"), float("inf")))
        self.assertFalse(equal(float("nan"), float("nan")))

    def test_unrelated_package_needs_no_calendar_model(self):
        # This injected package and adapter have no dates, fixtures, events, or move method.
        @dataclass
        class Observation:
            source: str = "independent-provider"
            observed_at: str = "now"

            def __bool__(self):
                return False

            def to_dict(self):
                return {"scalar": 7, "source": self.source, "observed_at": self.observed_at}

        class Pack:
            id, version = "custom.scalar.v1", "1"

            def parse(self, spec):
                return 7

            def valid_baseline(self, trial, before):
                return trial == 7

            def assess(self, trial, before, after):
                return evaluate({"scalar": trial}, {"scalar": 7}, after.source, after.observed_at)

        class Adapter:
            name, version, evidence_level = "custom", "1", "local_runner"
            profile = {}
            supported_trials = frozenset({"custom.scalar.v1"})

            def resource(self, run_id):
                return Resource(run_id, run_id)

            def provision(self, resource, trial):
                pass

            seed = provision
            apply = provision

            def read(self, resource, trial):
                return Observation()

            def cleanup(self, resource):
                pass

        spec = {"id": Pack.id, "fixture": {"scalar": 7}}
        result = run_trial(spec, [Adapter()], self.store, "custom", PackRegistry([Pack()]))
        self.assertEqual(result["candidates"][0]["verdict"], "pass")
        self.assertIsNotNone(result["candidates"][0]["evidence"]["before"])
        self.assertIsNotNone(result["candidates"][0]["evidence"]["after"])

    def test_wrong_observation_type_is_inconclusive(self):
        pack, trial = builtins().prepare(self.spec)
        self.assertEqual(pack.assess(trial, None, {})["verdict"], "inconclusive")
        self.assertFalse(pack.valid_baseline(trial, StorageSnapshot({}, "x", "now")))


if __name__ == "__main__":
    unittest.main()
