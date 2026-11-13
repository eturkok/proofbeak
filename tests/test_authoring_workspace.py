import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from proofbeak.adapters.filesystem import FilesystemAdapter
from proofbeak.adapters.scenario import ScenarioAdapter
from proofbeak.authoring import draft_scenario, operation_catalog, preview_suite
from proofbeak.service import ServiceError, Tenant, TrialService
from proofbeak.suites import example_suite
from proofbeak.web_sessions import BrowserSessions, COOKIE


class AuthoringWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.token = 'a' * 40
        self.service = TrialService(self.root / 'state', [Tenant('local', hashlib.sha256(self.token.encode()).hexdigest(), {
            'scenario:filesystem': lambda: ScenarioAdapter(FilesystemAdapter(self.root / 'objects'))})])
        self.service.start(background=False)
        self.addCleanup(self.service.close)

    def request(self, cookie=None, method='GET', headers=None):
        return SimpleNamespace(headers=headers or {}, cookies={COOKIE:cookie} if cookie else {},
                               method=method, base_url='http://localhost:8765/')

    def test_schema_catalog_and_expanded_preview_are_offline_and_do_not_reserve_work(self):
        catalog = operation_catalog(self.service.packs('local'))
        operation = next(o for o in catalog['provider_capabilities']['scenario:filesystem']['operations'] if o['id']=='object.get')
        self.assertTrue(operation['read_only'])
        self.assertIn('input_schema',operation)
        preview = preview_suite(self.service,'local',example_suite())
        self.assertTrue(preview['validation']['valid'])
        self.assertEqual(len(preview['plan']),3)
        self.assertFalse(preview['execution_performed'])
        self.assertEqual(self.service.history('local')['runs'],[])
        self.assertFalse((self.root/'objects').exists())

    def test_draft_keeps_explicit_user_expectations_and_uses_independent_observers(self):
        result = draft_scenario(self.service.packs('local'),['scenario:filesystem'],'Store the supplied value',
            'object.get',{'key':'probe'},['content'],'expected',actions=[{'op':'object.put','args':{'key':'probe','content':'expected'}}])
        self.assertEqual(result['expect'][0]['value'],'expected')
        self.assertEqual(result['expect'][0]['actual'],['after','result','content'])
        self.assertEqual(result['id'],'scenario.v1')
        self.assertTrue(self.service.validate_trial('local',result,['scenario:filesystem'])['valid'])
        with self.assertRaises(ValueError):
            draft_scenario(self.service.packs('local'),['scenario:filesystem'],'Bad observer','object.put',{},['content'],'expected')

    def test_invalid_preview_has_diagnostics_and_leaves_draft_untouched(self):
        suite = example_suite()
        suite['cases'][0]['test']['expect'][0]['actual']=['after','missing','value']
        original = copy.deepcopy(suite)
        preview = preview_suite(self.service,'local',suite)
        self.assertFalse(preview['validation']['valid'])
        self.assertEqual(suite,original)
        self.assertEqual(self.service.list_suites('local')['suites'],[])

    def test_sessions_preserve_tenant_and_require_csrf_for_cookie_writes(self):
        sessions = BrowserSessions(self.service)
        cookie, reply = sessions.create(self.token,self.request(method='POST'))
        self.assertNotIn(self.token,str(reply))
        self.assertEqual(sessions.authenticate(self.request(cookie)), 'local')
        with self.assertRaises(ServiceError) as error:
            sessions.authenticate(self.request(cookie,method='POST'))
        self.assertEqual(error.exception.code,'csrf_required')
        self.assertEqual(sessions.authenticate(self.request(cookie,method='POST',headers={'x-proofbeak-csrf':reply['csrf']})),'local')
        with self.assertRaises(ServiceError):
            sessions.authenticate(self.request(cookie,headers={'origin':'https://foreign.example'}))
        with self.assertRaises(ServiceError):
            sessions.create(self.token,self.request(headers={'sec-fetch-site':'cross-site'}))
        sessions.discard(self.request(cookie))
        self.assertIsNone(sessions.get(self.request(cookie)))

    def test_session_expiry_and_disabled_tenant_invalidate_browser_access(self):
        sessions = BrowserSessions(self.service)
        cookie,_ = sessions.create(self.token,self.request())
        expiry = sessions.sessions[cookie]['expires']
        with patch('proofbeak.web_sessions.time.monotonic',return_value=expiry+1):
            self.assertIsNone(sessions.get(self.request(cookie)))
        cookie,_ = sessions.create(self.token,self.request())
        self.service.purge_tenant('local')
        with self.assertRaises(ServiceError):
            sessions.authenticate(self.request(cookie))
