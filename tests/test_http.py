import hashlib
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.simulation import SimulationAdapter
from proofbeak.executor import SubprocessExecutor, WorkerLimits
from proofbeak.service import Tenant, TrialService

AVAILABLE = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx"))
SPEC = Path(__file__).resolve().parents[1] / "examples/storage.put_same_key.v1.json"


@unittest.skipUnless(AVAILABLE, "install .[agents] for REST boundary tests")
class HttpTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from proofbeak.interfaces.http import create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.tokens = {"a": "a" * 40, "b": "b" * 40}
        tenants = [Tenant(name, hashlib.sha256(token.encode()).hexdigest(), {
            "filesystem": lambda name=name: FilesystemAdapter(root / name),
        }) for name, token in self.tokens.items()]
        self.service = TrialService(root / "server", tenants)
        self.client = TestClient(create_app(self.service, background=False))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.spec = json.loads(SPEC.read_text())

    def headers(self, tenant="a", key="http-test"):
        return {"Authorization": f"Bearer {self.tokens[tenant]}", "Idempotency-Key": key}

    def submit(self):
        response = self.client.post("/runs", json={"test": self.spec, "candidates": ["filesystem"]},
                                    headers=self.headers())
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["run_id"]

    def test_authentication_is_required_for_all_data_routes(self):
        for route in ["/packs", "/runs/" + "a" * 32, "/evidence/" + "a" * 32 + ":0"]:
            self.assertEqual(self.client.get(route).status_code, 401)
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_custom_scenario_authoring_validation_and_run(self):
        from proofbeak.adapters.scenario import ScenarioAdapter
        from proofbeak.packs.scenario import ScenarioPack

        provider_root = self.service.root.parent / "scenario-objects"
        self.service.tenants["a"].providers["scenario:filesystem"] = lambda: ScenarioAdapter(FilesystemAdapter(provider_root))
        spec = ScenarioPack().example()
        payload = {"test": spec, "candidates": ["scenario:filesystem"]}
        self.assertEqual(self.client.post("/trials/validate", json=payload).status_code, 401)
        response = self.client.post("/trials/validate", json=payload, headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["valid"])
        self.assertFalse(response.json()["execution_performed"])
        self.assertEqual(self.service.history("a")["runs"], [])
        self.assertFalse(provider_root.exists())
        self.assertEqual(self.client.post("/trials/validate", json=payload, headers=self.headers("b")).status_code, 403)
        invalid = json.loads(json.dumps(payload))
        invalid["test"]["steps"][0]["op"] = "arbitrary.code"
        checked = self.client.post("/trials/validate", json=invalid, headers=self.headers()).json()
        self.assertFalse(checked["valid"])
        self.assertTrue(checked["issues"])
        self.assertEqual(self.client.post("/runs", json=invalid, headers=self.headers()).status_code, 422)
        self.assertFalse(provider_root.exists())
        created = self.client.post("/runs", json=payload, headers=self.headers())
        self.assertEqual(created.status_code, 202)
        self.service.process_next()
        report = self.client.get("/runs/" + created.json()["run_id"] + "/comparison", headers=self.headers()).json()
        self.assertEqual(report["candidates"][0]["verdict"], "pass")
        self.assertEqual(list(provider_root.iterdir()), [])

    def test_run_progress_comparison_and_evidence(self):
        run_id = self.submit()
        self.assertEqual(self.client.get(f"/runs/{run_id}", headers=self.headers()).json()["state"], "queued")
        self.service.process_next()
        result = self.client.get(f"/runs/{run_id}/comparison", headers=self.headers())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["candidates"][0]["verdict"], "pass")
        evidence_id = result.json()["candidates"][0]["evidence_ref"]
        self.assertEqual(self.client.get(f"/evidence/{evidence_id}", headers=self.headers()).status_code, 200)

    def test_foreign_run_comparison_evidence_and_cleanup_are_hidden(self):
        run_id = self.submit()
        self.service.process_next()
        for route in [f"/runs/{run_id}", f"/runs/{run_id}/comparison", f"/evidence/{run_id}:0"]:
            self.assertEqual(self.client.get(route, headers=self.headers("b")).status_code, 404)
        self.assertEqual(self.client.post(f"/runs/{run_id}/cleanup", headers=self.headers("b")).status_code, 404)
        self.assertEqual(self.client.post(f"/runs/{run_id}/cancel", headers=self.headers("b")).status_code, 404)

    def test_queued_run_can_be_cancelled_without_execution(self):
        run_id = self.submit()
        response = self.client.post(f"/runs/{run_id}/cancel", headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "cancelled")
        self.assertFalse(self.service.process_next())

    def test_history_lookup_revocation_and_audit_are_scoped(self):
        run_id = self.submit()
        self.service.process_next()
        found = self.client.post("/evidence/lookup", json={"test": self.spec, "candidates": ["filesystem"]},
                                 headers=self.headers())
        self.assertTrue(found.json()["reusable"])
        foreign = self.client.post("/evidence/lookup", json={"test": self.spec, "candidates": ["filesystem"]},
                                   headers=self.headers("b"))
        self.assertFalse(foreign.json()["reusable"])
        self.assertEqual(len(self.client.get("/runs", headers=self.headers()).json()["runs"]), 1)
        self.assertEqual(self.client.get("/runs", headers=self.headers("b")).json()["runs"], [])
        self.assertEqual(self.client.post(f"/runs/{run_id}/revoke", headers=self.headers("b")).status_code, 404)
        self.assertEqual(self.client.post(f"/runs/{run_id}/revoke", headers=self.headers()).status_code, 200)
        withdrawn = self.client.post("/evidence/lookup", json={"test": self.spec, "candidates": ["filesystem"]},
                                     headers=self.headers())
        self.assertFalse(withdrawn.json()["reusable"])
        self.assertTrue(self.client.get("/audit", headers=self.headers()).json()["events"])
        self.assertEqual(self.client.get("/audit", headers=self.headers("b")).json()["events"], [])

    def test_deletion_requires_terminal_owned_run_and_reserves_its_key(self):
        run_id = self.submit()
        self.assertEqual(self.client.delete(f"/runs/{run_id}", headers=self.headers()).status_code, 409)
        self.service.process_next()
        self.assertEqual(self.client.delete(f"/runs/{run_id}", headers=self.headers("b")).status_code, 404)
        self.assertEqual(self.client.delete(f"/runs/{run_id}", headers=self.headers()).status_code, 200)
        self.assertEqual(self.client.get(f"/runs/{run_id}", headers=self.headers()).status_code, 410)
        response = self.client.post("/runs", json={"test": self.spec, "candidates": ["filesystem"]},
                                    headers=self.headers())
        self.assertEqual(response.status_code, 410)

    def test_client_cannot_select_tenant_or_provider_credentials(self):
        for extra in [{"tenant_id": "b"}, {"profile": {"endpoint": "https://example.test"}},
                      {"access_token": "private-client-input"}]:
            response = self.client.post("/runs", json={"test": self.spec,
                "candidates": ["filesystem"], **extra}, headers=self.headers())
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("private-client-input", response.text)

    def test_unknown_provider_is_forbidden(self):
        response = self.client.post("/runs", json={"test": self.spec, "candidates": ["google-calendar"]},
                                    headers=self.headers())
        self.assertEqual(response.status_code, 403)

    def test_key_is_required_and_conflicts_are_explicit(self):
        self.assertEqual(self.client.post("/runs", json={"test": self.spec, "candidates": ["filesystem"]},
            headers={"Authorization": f'Bearer {self.tokens["a"]}'}).status_code, 422)
        run_id = self.submit()
        self.assertEqual(self.submit(), run_id)
        changed = {**self.spec, "fixture": {**self.spec["fixture"], "content": "different"}}
        response = self.client.post("/runs", json={"test": changed, "candidates": ["filesystem"]},
                                    headers=self.headers())
        self.assertEqual(response.status_code, 409)

    def test_pending_comparison_and_active_cleanup_return_conflict(self):
        run_id = self.submit()
        self.assertEqual(self.client.get(f"/runs/{run_id}/comparison", headers=self.headers()).status_code, 409)
        self.assertEqual(self.client.post(f"/runs/{run_id}/cleanup", headers=self.headers()).status_code, 409)

    def test_body_limit_is_enforced_before_json_parsing(self):
        response = self.client.post("/runs", content=b"x" * 65537,
                                    headers={**self.headers(), "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)

    def test_worker_executes_asynchronously(self):
        from fastapi.testclient import TestClient

        from proofbeak.interfaces.http import create_app

        asynchronous = TrialService(self.service.root.parent / "async-server",
                                    list(self.service.tenants.values()))
        with TestClient(create_app(asynchronous, background=True)) as client:
            response = client.post("/runs", json={"test": self.spec, "candidates": ["filesystem"]},
                                   headers=self.headers(key="async"))
            self.assertEqual(response.status_code, 202)
            run_id = response.json()["run_id"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                run = client.get(f"/runs/{run_id}", headers=self.headers()).json()
                if run["state"] == "completed":
                    break
                time.sleep(0.01)
            self.assertEqual(run["state"], "completed")

    def test_hung_worker_does_not_block_api_or_the_next_job(self):
        from fastapi.testclient import TestClient

        from proofbeak.interfaces.http import create_app

        tenant = Tenant("a", hashlib.sha256(self.tokens["a"].encode()).hexdigest(), {
            "simulation:hang": lambda: SimulationAdapter("hang"),
            "filesystem": self.service.tenants["a"].providers["filesystem"]})
        service = TrialService(self.service.root.parent / "bounded-server", [tenant],
                               executor=SubprocessExecutor(WorkerLimits(wall_seconds=2)))
        calendar = json.loads((SPEC.parent / "calendar.move_one.v1.json").read_text())
        with TestClient(create_app(service)) as client:
            first = client.post("/runs", json={"test": calendar, "candidates": ["simulation:hang"]},
                                headers=self.headers(key="hung")).json()["run_id"]
            second = client.post("/runs", json={"test": self.spec, "candidates": ["filesystem"]},
                                 headers=self.headers(key="following")).json()["run_id"]
            started = time.monotonic()
            self.assertEqual(client.get("/health").status_code, 200)
            self.assertLess(time.monotonic() - started, 1)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                done = client.get(f"/runs/{second}", headers=self.headers()).json()
                if done["state"] == "completed":
                    break
                time.sleep(.02)
            self.assertEqual(done["state"], "completed")
            interrupted = client.get(f"/runs/{first}", headers=self.headers()).json()
            self.assertEqual(interrupted["reason_code"], "trial_timed_out")
            self.assertEqual(interrupted["candidates"], [])


if __name__ == "__main__":
    if os.environ.get("PROOFBEAK_REQUIRE_INTERFACES") == "1" and not AVAILABLE:
        raise SystemExit("REST test dependencies missing")
    unittest.main()
