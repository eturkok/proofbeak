import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proofbeak.adapters.base import ProviderError
from proofbeak.adapters.capability import CapabilityAdapter
from proofbeak.capabilities import matches, validator
from proofbeak.connectors import build_connector
from proofbeak.connectors.rest import openapi_operations
from proofbeak.engine import recover_cleanup, run_trial
from proofbeak.models import fingerprint
from proofbeak.packs import builtins
from proofbeak.packs.scenario_v2 import CapabilityPack, evaluate
from proofbeak.store import Store
from proofbeak.worker_adapters import decode_adapter, encode_adapter


@unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'connectors extra is required')
class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'runs.sqlite3')
        self.addCleanup(self.store.close)
        self.backend = build_connector('capability:sqlite', {'kind': 'sqlite', 'connection': {'root': str(self.root / 'data')}})
        self.adapter = CapabilityAdapter(self.backend)
        self.spec = CapabilityPack().example()

    def run_case(self, spec=None, key='case'):
        return run_trial(spec or self.spec, [self.adapter], self.store, key)['candidates'][0]

    def test_committed_value_is_independently_observed_and_cleanup_replayed_safely(self):
        row = self.run_case()
        self.assertEqual((row['verdict'], row['cleanup_status']), ('pass', 'cleaned'))
        self.assertFalse(row['evidence']['before']['values']['row']['exists'])
        self.assertEqual(row['evidence']['after']['values']['row']['value'], 'hello')
        self.assertEqual(fingerprint(row['evidence']), row['evidence_hash'])
        self.assertEqual(list(self.backend.root.iterdir()), [])
        with patch.object(self.backend, 'invoke', side_effect=AssertionError('no duplicate execution')):
            self.assertEqual(self.run_case(), row)

    def test_changed_expectation_is_a_measured_failure(self):
        self.spec['expect'][0]['value'] = 'different'
        row = self.run_case()
        self.assertEqual(row['verdict'], 'fail')
        self.assertEqual(row['checks'][0]['observed'], 'hello')

    def test_rollback_preserves_committed_value(self):
        self.spec['setup'] = copy.deepcopy(self.spec['steps'])
        self.spec['steps'][0]['op'] = 'record.rollback'
        self.spec['steps'][0]['args']['value'] = 'discard me'
        self.spec['require'] = []
        row = self.run_case()
        self.assertEqual(row['verdict'], 'pass')
        self.assertTrue(row['evidence']['after']['captures']['steps']['write']['rolled_back'])

    def test_write_cannot_be_disguised_as_read(self):
        self.spec['observe'][0] = copy.deepcopy(self.spec['steps'][0])
        self.spec['observe'][0]['id'] = 'row'
        pack, trial = builtins().prepare(self.spec)
        with self.assertRaisesRegex(ValueError, 'read-only'):
            pack.validate_adapter(trial, self.adapter)
        self.assertFalse(self.backend.root.exists())

    def test_bad_input_and_forward_reference_reject_before_reservation(self):
        self.spec['steps'][0]['args']['value'] = 42
        with self.assertRaises(ValueError):
            self.run_case()
        self.assertFalse(self.backend.root.exists())
        self.spec = CapabilityPack().example()
        self.spec['steps'][0]['args']['value'] = {'ref': ['steps', 'missing', 'value']}
        with self.assertRaises(ValueError):
            builtins().prepare(self.spec)

    def test_schema_and_ranking_conditions_distinguish_fail_and_missing(self):
        definition = {'name': 'schema', 'actual': ['after', 'read', 'json'], 'op': 'schema',
                      'value': {'type': 'object', 'required': ['answer'], 'properties': {'answer': {'type': 'string'}}}}
        self.assertEqual(evaluate([definition], {'after': {'read': {'json': {'answer': 'ok'}}}}, 'fixture', 'now')['verdict'], 'pass')
        self.assertEqual(evaluate([definition], {'after': {'read': {'json': {'answer': 1}}}}, 'fixture', 'now')['verdict'], 'fail')
        self.assertEqual(evaluate([definition], {'after': {}}, 'fixture', 'now')['verdict'], 'inconclusive')
        definition.update(op='recall_at_k', value={'relevant': ['a', 'b'], 'id_field': 'id', 'k': 2, 'min_recall': 1})
        result = evaluate([definition], {'after': {'read': {'json': [{'id': 'a'}, {'id': 'a'}]}}}, 'fixture', 'now')
        self.assertEqual(result['verdict'], 'fail')
        self.assertEqual(result['checks'][0]['measured_recall'], .5)

    def test_schema_does_not_fetch_remote_refs(self):
        with self.assertRaisesRegex(ValueError, 'document-local'):
            validator({'$ref': 'https://example.test/private'})
        self.assertTrue(matches({'$defs': {'text': {'type': 'string'}}, '$ref': '#/$defs/text'}, 'ok'))

    def test_readonly_database_statement_is_enforced_by_database(self):
        settings = copy.deepcopy(self.backend.settings)
        settings['operations']['record.evil'] = {'input_schema': {'type': 'object', 'additionalProperties': False},
            'output_schema': {'type': 'object'}, 'read_only': True, 'binding': {'statement': 'DELETE FROM {table}'}}
        backend = build_connector('capability:guarded', settings)
        resource = backend.resource('a' * 32)
        backend.provision(resource, None)
        self.addCleanup(lambda: backend.cleanup(resource))
        backend.invoke(resource, 'record.put', {'key': 'keep', 'value': 'preserved'})
        with self.assertRaises(ProviderError):
            backend.invoke(resource, 'record.evil', {})
        self.assertEqual(backend.invoke(resource, 'record.get', {'key': 'keep'})['value'], 'preserved')

    def test_ownership_corruption_prevents_cleanup(self):
        resource = self.backend.resource('b' * 32)
        self.backend.provision(resource, None)
        with self.backend._db(resource) as db:
            db.execute("UPDATE owner SET scope='foreign'")
            db.commit()
        with self.assertRaises(ProviderError):
            self.backend.cleanup(resource)
        self.assertTrue(self.backend._file(resource).exists())

    def test_worker_codec_preserves_public_profile_and_actual_sql_execution(self):
        decoded = decode_adapter(encode_adapter(self.adapter))
        self.assertEqual(decoded.profile, self.adapter.profile)
        row = run_trial(self.spec, [decoded], self.store, 'decoded')['candidates'][0]
        self.assertEqual(row['verdict'], 'pass')

    def test_openapi_import_uses_selected_operations_and_rejects_remote_refs(self):
        doc = {'openapi': '3.1.0', 'paths': {'/items/{key}': {'get': {'operationId': 'getItem',
            'parameters': [{'name': 'key', 'in': 'path', 'required': True, 'schema': {'type': 'string'}}]}}}}
        operations = openapi_operations(doc, ['getItem'])
        self.assertEqual(operations['api.getitem']['binding'], {'method': 'GET', 'path': '/items/{key}'})
        self.assertTrue(operations['api.getitem']['read_only'])
        doc['paths']['/items/{key}']['get']['parameters'] = [{'$ref': 'https://example.test/parameters'}]
        with self.assertRaises(ValueError):
            openapi_operations(doc, ['getItem'])

    def test_eventual_checks_observe_repeatedly_and_retain_poll_provenance(self):
        self.spec['eventually'] = {'timeout_seconds': .2, 'interval_seconds': .01, 'stable_samples': 2}
        row = self.run_case()
        self.assertEqual(row['verdict'], 'pass')
        self.assertEqual(len(row['evidence']['after']['polls']), 2)
        self.assertTrue(all(p['verdict'] == 'pass' for p in row['evidence']['after']['polls']))
        self.assertEqual(sum(e['phase'] == 'steps' for e in row['evidence']['after']['events']), 1)

    def test_eventual_deadline_does_not_retry_writes_or_invent_success(self):
        self.spec['eventually'] = {'timeout_seconds': .05, 'interval_seconds': .01}
        self.spec['expect'][0]['value'] = 'never'
        row = self.run_case()
        self.assertEqual(row['verdict'], 'fail')
        self.assertGreater(len(row['evidence']['after']['polls']), 1)
        self.assertEqual(sum(e['phase'] == 'steps' for e in row['evidence']['after']['events']), 1)

    def test_recovery_can_remove_only_an_owned_or_absent_database(self):
        resource = self.backend.resource('c' * 32)
        self.backend.provision(resource, None)
        self.backend.cleanup(resource)
        self.backend.cleanup(resource)
        self.assertFalse(self.backend._file(resource).exists())
        self.assertEqual(recover_cleanup(self.store, [self.adapter]), [])

    def test_multi_statement_transaction_rollback_leaves_independent_reads_unchanged(self):
        settings = copy.deepcopy(self.backend.settings)
        definition = copy.deepcopy(settings['operations']['record.put'])
        definition['binding'] = {'statements': [
            'INSERT INTO {table}(key,value) VALUES (:key,:value)',
            "UPDATE {table} SET value='changed' WHERE key=:key"], 'rollback': True}
        settings['operations']['record.batch'] = definition
        backend = build_connector('capability:transactions', settings)
        resource = backend.resource('d' * 32)
        backend.provision(resource, None)
        self.addCleanup(lambda: backend.cleanup(resource))
        backend.invoke(resource, 'record.batch', {'key': 'probe', 'value': 'discarded'})
        self.assertFalse(backend.invoke(resource, 'record.get', {'key': 'probe'})['exists'])

    def test_remote_credentials_stay_outside_public_profile_and_scope_is_not_user_input(self):
        from capability_fixtures import rest_profile
        settings = rest_profile('https://example.test')
        settings['connection']['secret_env'] = {'Authorization': 'PROVIDER_AUTHORIZATION'}
        adapter = CapabilityAdapter(build_connector('capability:auth', settings, {'Authorization': 'Bearer private-value'}))
        encoded = encode_adapter(adapter)
        self.assertNotIn('private-value', str(adapter.profile))
        self.assertEqual(encoded['options']['secrets']['Authorization'], 'Bearer private-value')
        self.spec['steps'][0]['args']['scope'] = 'foreign'
        with self.assertRaises(ValueError):
            CapabilityPack().parse(self.spec)

    def test_mutating_rest_profiles_require_lifecycle_and_owned_paths(self):
        from capability_fixtures import rest_profile
        settings = rest_profile('https://example.test')
        del settings['lifecycle']
        with self.assertRaises(ValueError):
            build_connector('capability:unsafe', settings)
        settings = rest_profile('https://example.test')
        settings['operations']['record.put']['binding']['path'] = '/items/{key}'
        with self.assertRaises(ValueError):
            build_connector('capability:unsafe', settings)

    def test_discovery_is_schema_driven_and_has_no_provider_access(self):
        with patch.object(self.backend, 'invoke', side_effect=AssertionError('no I/O')):
            operations = self.adapter.describe_operations()
            self.assertTrue(all('input_schema' in op and 'output_schema' in op for op in operations))
            self.adapter.validate_scenario(CapabilityPack().parse(self.spec))
        self.assertFalse(self.backend.root.exists())


if __name__ == '__main__':
    unittest.main()
