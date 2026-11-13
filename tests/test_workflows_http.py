import hashlib
import importlib.util
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.scenario import ScenarioAdapter
from proofbeak.service import Tenant, TrialService
from proofbeak.suites import example_suite

AVAILABLE = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))


@unittest.skipUnless(AVAILABLE, "install agents for workflow HTTP tests")
class WorkflowHttpTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from proofbeak.interfaces.http import create_app
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.token = "a" * 40
        tenants = [Tenant("a", hashlib.sha256(self.token.encode()).hexdigest(), {
            "scenario:filesystem": lambda: ScenarioAdapter(FilesystemAdapter(root / "objects"))})]
        self.service = TrialService(root / "state", tenants)
        self.client = TestClient(create_app(self.service, background=False))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {"Authorization": "Bearer " + self.token, "Idempotency-Key": "suite-http"}

    def submit(self):
        response = self.client.post("/suites", json={"suite": example_suite()}, headers=self.headers)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["suite_id"]

    def test_actual_http_workflow_exports_and_idempotent_retry(self):
        valid = self.client.post("/suites/validate", json={"suite": example_suite()}, headers=self.headers)
        self.assertTrue(valid.json()["valid"])
        self.assertFalse(self.service.process_next())
        suite_id = self.submit()
        while self.service.process_next():
            pass
        result = self.client.get("/suites/" + suite_id, headers=self.headers)
        self.assertEqual(result.json()["gate"], "pass")
        self.assertEqual(self.submit(), suite_id)
        for format, marker in (("json", '"schema_version"'), ("html", "<!doctype html>"),
                               ("junit", "<testsuite"), ("summary", "Gate: pass")):
            exported = self.client.get(f"/suites/{suite_id}/export?format={format}", headers=self.headers)
            self.assertEqual(exported.status_code, 200)
            self.assertIn(marker, exported.text)
            self.assertIn("attachment", exported.headers["content-disposition"])
        compared = self.client.post("/comparisons", json={"baseline": suite_id, "current": suite_id, "kind": "suite"}, headers=self.headers)
        self.assertEqual(compared.json()["gate"], "pass")
        self.assertFalse(self.service.process_next())

    def test_authentication_and_unknown_suite_ids(self):
        self.assertEqual(self.client.get("/suites").status_code, 401)
        self.assertEqual(self.client.post("/suites", json={"suite": example_suite()}).status_code, 401)
        self.assertEqual(self.client.get("/suites/" + "b" * 32, headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post("/suites/validate", json={"suite": example_suite(), "extra": True}, headers=self.headers).status_code, 422)

    def test_browser_login_reload_csrf_logout_and_cookie_attributes(self):
        login = self.client.post('/ui/login',json={'token':self.token},headers={'Origin':'http://testserver'})
        self.assertEqual(login.status_code,200,login.text)
        cookie = login.headers['set-cookie']
        self.assertIn('HttpOnly',cookie)
        self.assertIn('SameSite=strict',cookie)
        self.assertNotIn(self.token,cookie)
        self.assertEqual(self.client.get('/ui/session').json()['tenant'],'a')
        self.assertEqual(self.client.get('/authoring/catalog').status_code,200)
        blocked = self.client.post('/suites/validate',json={'suite':example_suite()})
        self.assertEqual(blocked.status_code,403)
        csrf = login.json()['csrf']
        allowed = self.client.post('/authoring/preview',json={'suite':example_suite()},headers={'X-Proofbeak-CSRF':csrf})
        self.assertTrue(allowed.json()['validation']['valid'])
        self.assertFalse(allowed.json()['execution_performed'])
        cross = self.client.post('/suites/validate',json={'suite':example_suite()},headers={'X-Proofbeak-CSRF':csrf,'Origin':'https://foreign.example'})
        self.assertEqual(cross.status_code,403)
        self.assertEqual(self.client.get('/ui/').status_code,200)
        self.assertIn("script-src 'self'",self.client.get('/ui/').headers['content-security-policy'])
        self.assertEqual(self.client.get('/ui/assets/app.js').status_code,200)
        self.assertEqual(self.client.post('/ui/logout',json={},headers={'X-Proofbeak-CSRF':csrf}).status_code,200)
        self.assertFalse(self.client.get('/ui/session').json()['authenticated'])
        self.assertEqual(self.client.get('/suites').status_code,401)

    def test_normal_tenant_cannot_change_operator_profiles(self):
        response=self.client.post('/studio/providers',json={'name':'capability:new','settings':{'kind':'sqlite','connection':{}}},headers=self.headers)
        self.assertEqual(response.status_code,403)

    @unittest.skipUnless(importlib.util.find_spec("cryptography"), "install signing for signed HTTP exports")
    def test_signed_suite_export_and_explicit_public_key_verification(self):
        from proofbeak.bundles import generate_keys
        private, public = self.service.root.parent / "private.json", self.service.root.parent / "public.json"
        document = generate_keys(private, public)
        suite_id = self.submit()
        while self.service.process_next():
            pass
        self.assertEqual(self.client.post(f"/suites/{suite_id}/bundle", headers=self.headers).status_code, 409)
        with patch.dict(os.environ, {"PROOFBEAK_SIGNING_KEY_FILE": str(private)}):
            status = self.client.get("/signing", headers=self.headers).json()
            self.assertTrue(status["configured"])
            self.assertNotIn("private_key", status["public_key"])
            bundle = self.client.post(f"/suites/{suite_id}/bundle", headers=self.headers)
            self.assertEqual(bundle.status_code, 200, bundle.text)
            verified = self.client.post("/bundles/verify", json={"bundle": bundle.json(), "public_key": document}, headers=self.headers)
            self.assertTrue(verified.json()["signature_valid"])
            self.assertEqual(verified.json()["acceptance_gate"], "pass")
            corrupted = bundle.json()
            corrupted["payload"]["report"]["name"] = "modified"
            rejected = self.client.post("/bundles/verify", json={"bundle": corrupted, "public_key": document}, headers=self.headers)
            self.assertEqual(rejected.status_code, 422)


if __name__ == "__main__":
    if os.environ.get("PROOFBEAK_REQUIRE_INTERFACES") and not AVAILABLE:
        raise SystemExit("Workflow HTTP qualification requires agents dependencies")
    unittest.main()
