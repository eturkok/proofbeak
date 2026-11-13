import copy
import importlib.util
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.scenario import ScenarioAdapter
from proofbeak.bundles import create_bundle, generate_keys, read_key, verify_bundle
from proofbeak.store import Store
from proofbeak.suites import example_suite, run_suite

AVAILABLE = importlib.util.find_spec("cryptography") is not None


@unittest.skipUnless(AVAILABLE, "install signing for Ed25519 evidence tests")
class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.private, self.public = self.root / "private.json", self.root / "public.json"
        generate_keys(self.private, self.public)
        store = Store(self.root / "runs.sqlite3")
        try:
            self.report = run_suite(example_suite(), lambda names: [ScenarioAdapter(FilesystemAdapter(self.root / "objects"))], store, "signed")
        finally:
            store.close()

    def test_portable_signature_and_independently_pinned_key(self):
        bundle = create_bundle(self.report, self.private)
        verified = verify_bundle(json.loads(json.dumps(bundle)), self.public)
        self.assertTrue(verified["signature_valid"])
        self.assertTrue(verified["content_integrity_valid"])
        self.assertEqual(verified["acceptance_gate"], "pass")
        self.assertEqual(verified["observations"], 3)
        self.assertFalse(verified["trusted_timestamp"])
        self.assertEqual(verified["current_revocation_status"], "not_checked_offline")
        self.assertEqual(verify_bundle(bundle, json.loads(self.public.read_text())), verified)

    def test_self_signed_substitution_and_payload_signature_tampering_rejected(self):
        bundle = create_bundle(self.report, self.private)
        for target in ("name", "signature", "format"):
            tampered = copy.deepcopy(bundle)
            if target == "name":
                tampered["payload"]["report"]["name"] = "tampered"
            else:
                tampered[target] = "tampered"
            with self.assertRaises(ValueError):
                verify_bundle(tampered, self.public)
        other_private, other_public = self.root / "other-private.json", self.root / "other-public.json"
        generate_keys(other_private, other_public)
        with self.assertRaises(ValueError):
            verify_bundle(create_bundle(self.report, other_private), self.public)

    def test_raw_corruption_and_missing_raw_records_cannot_be_signed(self):
        for mutation in ("corrupt", "missing"):
            report = copy.deepcopy(self.report)
            candidate = report["entries"][0]["report"]["candidates"][0]
            candidate["evidence"] = {} if mutation == "corrupt" else None
            with self.assertRaises(ValueError):
                create_bundle(report, self.private)

    def test_expiry_is_reported_without_invalidating_an_authentic_historical_signature(self):
        bundle = create_bundle(self.report, self.private)
        result = verify_bundle(bundle, self.public, datetime.now(UTC) + timedelta(days=2))
        self.assertTrue(result["signature_valid"])
        self.assertTrue(result["historical_only"])
        self.assertEqual(result["expired_observations"], 3)

    def test_private_file_permissions_and_mismatched_key_material_are_rejected(self):
        self.private.chmod(0o644)
        with self.assertRaises(ValueError):
            read_key(self.private, private=True)
        self.private.chmod(0o600)
        value = json.loads(self.private.read_text())
        value["private_key"] = "A" * 43 + "="
        self.private.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            read_key(self.private, private=True)

    def test_existing_keys_and_export_paths_are_not_replaced(self):
        original = self.private.read_bytes()
        with self.assertRaises(ValueError):
            generate_keys(self.private, self.public)
        self.assertEqual(self.private.read_bytes(), original)
        with self.assertRaises(ValueError):
            generate_keys(self.root / "same", self.root / "same")

    def test_simulated_observations_remain_labelled_after_valid_signature(self):
        from proofbeak.models import fingerprint
        report = copy.deepcopy(self.report)
        candidate = report["entries"][0]["report"]["candidates"][0]
        candidate["evidence_level"] = "simulation"
        candidate["evidence_hash"] = fingerprint(candidate["evidence"])
        result = verify_bundle(create_bundle(report, self.private), self.public)
        self.assertEqual(result["simulated_observations"], 1)
        self.assertEqual(result["acceptance_gate"], "inconclusive")


if __name__ == "__main__":
    if os.environ.get("PROOFBEAK_REQUIRE_SIGNING") and not AVAILABLE:
        raise SystemExit("Signed evidence qualification requires cryptography")
    unittest.main()
