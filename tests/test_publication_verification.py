import io
import json
import subprocess
import unittest
from unittest.mock import patch

from verify_pypi import install_published, verify


class PublicationVerificationTests(unittest.TestCase):
    def test_only_public_index_propagation_misses_are_retried(self):
        missing = subprocess.CompletedProcess([], 1, "", "No matching distribution found for proofbeak==0.8.0")
        ready = subprocess.CompletedProcess([], 0, "", "")
        with patch("verify_pypi.subprocess.run", side_effect=[missing, ready]) as run, patch("verify_pypi.time.sleep"):
            install_published(["pip"], cwd="/tmp", env={})
            self.assertEqual(run.call_count, 2)
        for output in ("THESE PACKAGES DO NOT MATCH THE HASHES", "ResolutionImpossible", "CERTIFICATE_VERIFY_FAILED"):
            with self.subTest(output=output), patch("verify_pypi.subprocess.run", return_value=
                    subprocess.CompletedProcess([], 1, "", output)) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    install_published(["pip"], cwd="/tmp", env={})
                self.assertEqual(run.call_count, 1)

    def test_index_propagation_timeout_is_bounded(self):
        missing = subprocess.CompletedProcess([], 1, "", "No matching distribution found for proofbeak==0.8.0")
        with patch("verify_pypi.subprocess.run", return_value=missing) as run, \
                patch("verify_pypi.time.monotonic", side_effect=[0, 100]):
            with self.assertRaises(subprocess.CalledProcessError):
                install_published(["pip"], cwd="/tmp", env={}, timeout=90)
            self.assertEqual(run.call_count, 1)

    def test_different_or_extra_registry_files_cannot_start_the_install_verification(self):
        manifest = {"name": "proofbeak", "version": "0.6.0", "artifacts": {
            "proofbeak.whl": {"sha256": "a" * 64}, "proofbeak.tar.gz": {"sha256": "b" * 64}}}
        cases = [
            {"proofbeak.whl": "c" * 64, "proofbeak.tar.gz": "b" * 64},
            {"proofbeak.whl": "a" * 64, "proofbeak.tar.gz": "b" * 64, "unexpected.whl": "d" * 64},
        ]
        for files in cases:
            body = json.dumps({"urls": [{"filename": filename, "digests": {"sha256": digest}}
                                        for filename, digest in files.items()]}).encode()
            with self.subTest(files=files), patch("verify_pypi.urlopen", return_value=io.BytesIO(body)), \
                    patch("verify_pypi.venv.create") as install:
                with self.assertRaises(AssertionError):
                    verify(manifest)
                install.assert_not_called()
