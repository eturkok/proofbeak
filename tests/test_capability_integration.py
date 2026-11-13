"""Actual REST, MCP stdio/HTTP, subprocess, SQL and Redis integration qualification."""

import asyncio
import copy
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from capability_fixtures import CapabilityHTTPServer, mcp_profile, operation, rest_profile
from proofbeak.adapters.capability import CapabilityAdapter
from proofbeak.connectors import build_connector
from proofbeak.engine import run_trial
from proofbeak.executor import SubprocessExecutor
from proofbeak.models import fingerprint
from proofbeak.packs.scenario_v2 import CapabilityPack
from proofbeak.server import provider_factory
from proofbeak.service import Tenant, TrialService
from proofbeak.store import Store

ROOT = Path(__file__).resolve().parents[1]
AVAILABLE = all(importlib.util.find_spec(name) for name in ('jsonschema', 'mcp', 'fastapi', 'httpx'))
REQUIRED = os.environ.get('PROOFBEAK_REQUIRE_CAPABILITIES') == '1'
if REQUIRED and not AVAILABLE:
    raise RuntimeError('capability qualification dependencies are required')


@unittest.skipUnless(AVAILABLE, 'install agents,connectors,dev extras for protocol qualification')
class CapabilityIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.http = CapabilityHTTPServer()
        except OSError:
            if REQUIRED:
                raise
            raise unittest.SkipTest('loopback sockets unavailable')
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = 'http://127.0.0.1:' + str(cls.http.server_port)
        cls.reports = []

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        for timer in cls.http.pending:
            timer.join()
        report = Path(os.environ.get('PROOFBEAK_CAPABILITY_REPORT', 'reports/capability-qualification.json'))
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({'scope': 'synthetic real-protocol qualification; no external model quality claim',
                                     'cases': cls.reports}, indent=2) + '\n')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'runs.sqlite3')
        self.addCleanup(self.store.close)
        self.spec = CapabilityPack().example()

    def check_row(self, report, expected='pass'):
        row = report['candidates'][0]
        self.assertEqual(row['verdict'], expected, json.dumps(row))
        self.assertEqual(row['cleanup_status'], 'cleaned')
        self.assertEqual(fingerprint(row['evidence']), row['evidence_hash'])
        self.reports.append({'provider': row['provider'], 'verdict': row['verdict'], 'cleanup': row['cleanup_status'],
                             'evidence_hash': row['evidence_hash'], 'pack': row['pack_id']})
        return row

    def test_rest_crud_eventual_observation_retry_and_ownership(self):
        self.http.delay = .08
        self.addCleanup(setattr, self.http, 'delay', 0)
        self.spec['eventually'] = {'timeout_seconds': 2, 'interval_seconds': .02, 'stable_samples': 2}
        adapter = CapabilityAdapter(build_connector('capability:rest', rest_profile(self.origin)))
        report = run_trial(self.spec, [adapter], self.store, 'rest')
        row = self.check_row(report)
        self.assertGreater(len(row['evidence']['after']['polls']), 2)
        journal = list(self.http.journal)
        self.assertEqual(run_trial(self.spec, [adapter], self.store, 'rest'), report)
        self.assertEqual(journal, self.http.journal)
        self.assertEqual(self.http.scopes, {})
        self.spec['expect'][0]['value'] = 'deliberately wrong'
        self.spec['eventually']['timeout_seconds'] = .2
        self.check_row(run_trial(self.spec, [adapter], self.store, 'rest-fail'), 'fail')

    def test_same_condition_compares_rest_and_database(self):
        self.http.delay = 0
        adapters = [CapabilityAdapter(build_connector('capability:rest', rest_profile(self.origin))),
                    CapabilityAdapter(build_connector('capability:sqlite', {'kind': 'sqlite', 'connection': {'root': str(self.root / 'db')}}))]
        report = run_trial(self.spec, adapters, self.store, 'compare')
        self.assertEqual([row['verdict'] for row in report['candidates']], ['pass', 'pass'])
        for row in report['candidates']:
            self.assertEqual(row['cleanup_status'], 'cleaned')

    def test_mcp_stdio_target_and_subprocess_worker(self):
        profile = mcp_profile(sys.executable, ROOT / 'tests/capability_mcp_fixture.py', self.root / 'target')
        adapter = CapabilityAdapter(build_connector('capability:mcp', profile))
        result = SubprocessExecutor().run(self.spec, [adapter], self.root / 'worker.sqlite3', 'mcp-worker')
        self.assertIsNone(result.reason, result.reason)
        self.check_row(result.payload['report'])
        self.assertEqual(list((self.root / 'target').iterdir()), [])

    def test_mcp_streamable_http_target(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with (self.root / 'target.log').open('w') as log:
            process = subprocess.Popen([sys.executable, str(ROOT / 'tests/capability_mcp_fixture.py'),
                str(self.root / 'target-http'), str(port)], stdout=log, stderr=log)
            self.addCleanup(lambda: (process.terminate(), process.wait(timeout=10)))
            deadline = time.monotonic() + 15
            while True:
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    self.assertIsNone(process.poll(), 'MCP target exited')
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.05)
            profile = mcp_profile(None, None, None, 'http', f'http://127.0.0.1:{port}/mcp')
            self.check_row(run_trial(self.spec, [CapabilityAdapter(build_connector('capability:mcp_http', profile))], self.store, 'mcp-http'))
            self.assertEqual(list((self.root / 'target-http').iterdir()), [])

    def test_postgres_commit_and_rollback_on_actual_database(self):
        dsn = os.environ.get('PROOFBEAK_TEST_POSTGRES_DSN')
        if not dsn:
            if REQUIRED:
                self.fail('PostgreSQL service is required')
            self.skipTest('PostgreSQL test DSN not configured')
        profile = {'kind': 'postgres', 'connection': {'secret_env': {'dsn': 'PROOFBEAK_TEST_POSTGRES_DSN'}}}
        adapter = CapabilityAdapter(build_connector('capability:postgres', profile, {'dsn': dsn}))
        self.check_row(run_trial(self.spec, [adapter], self.store, 'postgres'))
        self.spec['setup'] = copy.deepcopy(self.spec['steps'])
        self.spec['require'] = []
        self.spec['steps'][0]['op'] = 'record.rollback'
        self.spec['steps'][0]['args']['value'] = 'rolled back'
        self.check_row(run_trial(self.spec, [adapter], self.store, 'postgres-rollback'))
        self.assertNotIn(dsn, json.dumps(adapter.profile))

    def test_real_redis_stream_and_async_consumer(self):
        if not os.environ.get('PROOFBEAK_TEST_REDIS'):
            if REQUIRED:
                self.fail('Redis service is required')
            self.skipTest('Redis test service not configured')
        backend = build_connector('capability:redis', {'kind': 'redis', 'connection': {'host': '127.0.0.1', 'port': 6379}})
        stop, errors = threading.Event(), []
        def consume():
            processed = set()
            try:
                while not stop.wait(.02):
                    for key in backend.command('KEYS', 'proofbeak:*:events'):
                        for identifier, fields in backend.command('XRANGE', key, '-', '+', 'COUNT', 64):
                            if (key, identifier) not in processed:
                                processed.add((key, identifier))
                                payload = json.loads(dict(zip(fields[::2], fields[1::2]))['payload'])
                                backend.command('XADD', key.removesuffix(':events') + ':results', '*', 'payload', json.dumps({'processed': payload['value']}))
            except Exception as error:
                errors.append(type(error).__name__)
        thread = threading.Thread(target=consume)
        thread.start()
        try:
            spec = {'id': 'scenario.v2', 'name': 'Published event is independently processed', 'fixture': {}, 'setup': [],
                'steps': [{'id': 'publish', 'op': 'stream.publish', 'args': {'channel': 'events', 'payload': {'value': 'hello'}}}],
                'observe': [{'id': 'result', 'op': 'stream.read', 'args': {'channel': 'results'}}], 'require': [],
                'expect': [{'name': 'processed', 'actual': ['after', 'result', 'messages', 0, 'payload', 'processed'], 'op': 'eq', 'value': 'hello'}],
                'eventually': {'timeout_seconds': 3, 'interval_seconds': .03}}
            self.check_row(run_trial(spec, [CapabilityAdapter(backend)], self.store, 'redis'))
        finally:
            stop.set()
            thread.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(backend.command('KEYS', 'proofbeak:*'), [])

    def test_search_recall_and_model_output_contract_over_actual_http(self):
        for kind, op, arguments, binding, condition in [
            ('search', 'catalog.search', {'q': 'vector'}, {'method': 'GET', 'path': '/search', 'query': {'q': {'arg': 'q'}},
                'project': {'matches': ['json', 'matches']}}, {'actual': ['after', 'result', 'matches'], 'op': 'recall_at_k',
                    'value': {'relevant': ['vector'], 'id_field': 'id', 'k': 3, 'min_recall': 1}}),
            ('model', 'model.generate', {'prompt': 'hello'}, {'method': 'POST', 'path': '/v1/chat/completions',
                'body': {'messages': [{'role': 'user', 'content': {'arg': 'prompt'}}]},
                'project': {'answer': ['json', 'choices', 0, 'message', 'content']}},
                {'actual': ['after', 'result', 'answer'], 'op': 'contains', 'value': 'hello'})]:
            schema = {key: {'type': 'string'} for key in arguments}
            profile = {'kind': kind, 'connection': {'endpoint': self.origin}, 'operations': {op: operation(schema, binding, True)}}
            spec = {'id': 'scenario.v2', 'name': kind + ' contract', 'fixture': {}, 'setup': [], 'steps': [],
                'observe': [{'id': 'result', 'op': op, 'args': arguments}], 'require': [], 'expect': [{'name': 'quality', **condition}]}
            self.check_row(run_trial(spec, [CapabilityAdapter(build_connector('capability:' + kind, profile))], self.store, kind))

    def test_actual_proofbeak_mcp_drives_capability_through_api_and_worker(self):
        token = 'a' * 40
        settings = {'kind': 'sqlite', 'connection': {}}
        factory = provider_factory('capability:sqlite', settings, self.root, 'tenant')
        service = TrialService(self.root / 'state', [Tenant('tenant', hashlib.sha256(token.encode()).hexdigest(), {'capability:sqlite': factory})])
        service.start()
        self.addCleanup(service.close)
        from proofbeak.interfaces.http import create_app
        import uvicorn
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(service), host='127.0.0.1', port=port, log_level='error', lifespan='off'))
        thread = threading.Thread(target=server.run)
        thread.start()
        def close():
            server.should_exit = True
            thread.join(timeout=10)
        self.addCleanup(close)
        deadline = time.monotonic() + 10
        while not server.started:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)
        async def workflow():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=sys.executable, args=['-m', 'proofbeak.interfaces.mcp'],
                env={**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PROOFBEAK_API_URL': f'http://127.0.0.1:{port}', 'PROOFBEAK_API_TOKEN': token})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    async def call(name, arguments):
                        response = await session.call_tool(name, arguments)
                        self.assertFalse(response.isError, name)
                        return response.structuredContent
                    discovered = await call('list_trial_packs', {})
                    self.assertTrue(any(p['id'] == 'scenario.v2' for p in discovered['packages']))
                    args = {'test': self.spec, 'candidates': ['capability:sqlite']}
                    self.assertTrue((await call('validate_trial', args))['valid'])
                    created = await call('run_trial', {**args, 'idempotency_key': 'v2-mcp'})
                    limit = time.monotonic() + 20
                    while (await call('get_run', {'run_id': created['run_id']}))['state'] not in {'completed', 'interrupted'}:
                        self.assertLess(time.monotonic(), limit)
                        await asyncio.sleep(.03)
                    comparison = await call('compare_results', {'run_id': created['run_id']})
                    row = comparison['candidates'][0]
                    self.assertEqual(row['verdict'], 'pass')
                    self.assertEqual(row['cleanup_status'], 'cleaned')
                    evidence = await call('get_evidence', {'evidence_id': row['evidence_ref']})
                    self.assertEqual(fingerprint(evidence['evidence']), row['evidence_hash'])
                    self.assertEqual((await call('run_trial', {**args, 'idempotency_key': 'v2-mcp'}))['run_id'], created['run_id'])
        asyncio.run(workflow())


if __name__ == '__main__':
    unittest.main()
