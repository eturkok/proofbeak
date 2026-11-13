"""Operator onboarding imports closed profiles without contacting providers or exposing keys."""

import contextlib
import hashlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proofbeak.server import configured_service, load_service, main


class ServerInitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "server.json"
        self.profiles = self.root / "providers.json"
        self.args = ["init", "--config", str(self.config), "--state", str(self.root / "state")]

    def initialize(self, profiles=None):
        args = self.args
        if profiles is not None:
            self.profiles.write_text(json.dumps(profiles))
            args = [*args, "--providers", str(self.profiles)]
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            self.assertEqual(main(args), 0)
        return stream.getvalue().split("API key (shown once): ")[1].strip()

    def test_imported_profile_is_usable_with_private_config_and_one_time_key(self):
        profiles = {"filesystem": {}, "webdav:example": {
            "endpoint": "https://storage.example", "username": "test-user", "password_env": "TEST_DAV_PASSWORD"}}
        with patch("proofbeak.adapters.http.Transport.request", side_effect=AssertionError("network during init")):
            token = self.initialize(profiles)
            service = load_service(self.config)
        self.addCleanup(service.close)
        config = json.loads(self.config.read_text())
        self.assertEqual(config["tenants"][0]["providers"], profiles)
        self.assertEqual(config["tenants"][0]["token_sha256"], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, self.config.read_text())
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)

    def test_default_profile_remains_filesystem_and_existing_config_is_not_replaced(self):
        self.initialize()
        original = self.config.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(FileExistsError):
            main(self.args)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(json.loads(original)["tenants"][0]["providers"], {"filesystem": {}})

    def test_invalid_profiles_do_not_issue_key_or_create_config(self):
        base = {"endpoint": "https://storage.example", "username": "test-user", "password_env": "TEST_PASSWORD"}
        invalid = [[], {}, {"arbitrary.module:Adapter": {}}, {"filesystem": {"command": "example"}},
                   {"webdav:test": {**base, "endpoint": "http://remote.example"}},
                   {"webdav:test": {**base, "password_env": "secret value"}},
                   {"webdav:test": {**base, "password": "never-import-raw-secrets"}},
                   {"webdav:test": {**base, "endpoint": "https://user:password@storage.example"}},
                   {"filesystem": []}]
        for profiles in invalid:
            with self.subTest(profiles=profiles):
                self.profiles.write_text(json.dumps(profiles))
                with contextlib.redirect_stdout(io.StringIO()) as output, \
                        contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit):
                    main([*self.args, "--providers", str(self.profiles)])
                self.assertFalse(self.config.exists())
                self.assertEqual(output.getvalue(), "")
                self.assertNotIn("never-import-raw-secrets", errors.getvalue())

    def test_oversized_profile_file_is_rejected_before_parsing(self):
        self.profiles.write_text(" " * 65537)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main([*self.args, "--providers", str(self.profiles)])
        self.assertFalse(self.config.exists())

    def test_configuration_check_neither_creates_state_nor_contacts_providers(self):
        self.initialize({"filesystem": {}, "webdav:test": {
            "endpoint": "https://storage.example", "username": "test-user", "password_env": "TEST_DAV_PASSWORD"}})
        with patch("proofbeak.adapters.http.Transport.request", side_effect=AssertionError("network during check")), \
                patch.dict("os.environ", {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["check", "--config", str(self.config)]), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result["valid"])
        self.assertFalse(result["providers_configured"])
        self.assertFalse((self.root / "state").exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["check", "--config", str(self.config), "--require-providers"]), 2)

    def test_invalid_configuration_is_rejected_before_state_creation(self):
        self.initialize()
        original = json.loads(self.config.read_text())
        tenant = original["tenants"][0]
        invalid = [[], {**original, "tenants": []}, {**original, "tenants": [tenant, tenant]},
                   {**original, "tenants": [{**tenant, "id": "other"}, tenant]},
                   {**original, "tenants": [{**tenant, "providers": []}]},
                   {**original, "tenants": [{**tenant, "token_sha256": "raw-secret-canary"}]},
                   {**original, "execution": {"command": "arbitrary"}},
                   {**original, "execution": []}, {**original, "state_dir": []}]
        for config in invalid:
            with self.subTest(config=config):
                self.config.write_text(json.dumps(config))
                with contextlib.redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(main(["check", "--config", str(self.config)]), 3)
                self.assertNotIn("raw-secret-canary", errors.getvalue())
                self.assertFalse((self.root / "state").exists())

    def test_duplicate_json_keys_are_rejected(self):
        self.config.write_text('{"state_dir":"first","state_dir":"second","tenants":[]}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            configured_service(self.config)
