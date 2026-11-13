import io
import json
import unittest
from unittest.mock import patch

from verify_pypi import verify


class PublicationVerificationTests(unittest.TestCase):
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
