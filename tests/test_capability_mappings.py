import unittest
from unittest.mock import patch
from datetime import date
from adsb_ingest.capability_mappings import mapping_input, model_phrase, normalize_phrase, effective_mappings, evidence_snapshot
from adsb_ingest.maintenance_map import capability_match


class CapabilityMappingTests(unittest.TestCase):
    def test_shared_phrase_exactness_and_local_precedence(self):
        cap = dict(model='  Airbus  EC135 ', limitation='Line only', capability_kind='AIRCRAFT',
                   company_site_id=1, approval_status='VALID', is_line_maintenance=True)
        shared = dict(normalized_phrase='airbus ec135', aircraft_type_code='EC35', active=True,
                      match_level='REVIEWED_TYPE', variant_scope='')
        self.assertEqual(normalize_phrase(model_phrase(cap)), 'airbus ec135')
        self.assertEqual(model_phrase({'model':' ', 'limitation':'MBB-BK117 SERIES'}), 'MBB-BK117 SERIES')
        chosen = effective_mappings(cap, [], [shared])
        self.assertEqual(chosen[0]['origin'], 'SHARED')
        self.assertEqual(capability_match({**cap,'mappings':chosen}, 'EC35', date.today()), 'SITE_MATCH')
        self.assertEqual(effective_mappings({**cap,'model':'Airbus EC135 variant'}, [], [shared]), [])
        self.assertEqual(effective_mappings({**cap,'model':'Airbus EC135 / EC145'}, [], [shared]), [])
        local = dict(shared, evidence_snapshot=evidence_snapshot(cap), active=False)
        chosen = effective_mappings(cap, [local], [shared])
        self.assertEqual(chosen[0]['origin'], 'LOCAL')
        self.assertEqual(capability_match({**cap,'mappings':chosen}, 'EC35', date.today()), 'NO_RECORDED_MATCH')
        changed = {**cap,'limitation':'Restricted line maintenance'}
        chosen = effective_mappings(changed, [{**local,'active':True}], [shared])
        self.assertTrue(chosen[0]['stale'])
        self.assertEqual(capability_match({**changed,'mappings':chosen}, 'EC35', date.today()), 'STALE_MAPPING')
        for changes, expected in [({'company_site_id':None},'COMPANY_MATCH'),
                                  ({'approval_status':'SUSPENDED'},'APPROVAL_NOT_CURRENT')]:
            self.assertEqual(capability_match({**cap,**changes,'mappings':effective_mappings(cap, [], [shared])}, 'EC35', date.today()), expected)
        family = effective_mappings(cap, [], [{**shared,'match_level':'POSSIBLE_FAMILY'}])
        self.assertEqual(capability_match({**cap,'mappings':family}, 'EC35', date.today()), 'POSSIBLE_FAMILY')

    def test_api_roles_and_csrf(self):
        from adsb_ingest.webapp import create_app
        from test_webapp import FakeAdminStore, FakeAnalyticsStore, FakeNaturalLanguage, FakeAccessStore
        with patch.dict('os.environ', {'HELIGENT_AUTH_PROXY_SECRET':'s'*40,'HELIGENT_BOOTSTRAP_ADMIN_EMAILS':''}):
            app = create_app(start_worker=False,store=FakeAdminStore(),analytics_store=FakeAnalyticsStore(),natural_language=FakeNaturalLanguage(),access_store=FakeAccessStore(),auth_mode='NGROK')
            client = app.test_client()
            headers = {'X-Heligent-Proxy-Secret':'s'*40,'X-Heligent-Auth-Email':'viewer@example.com','X-Requested-With':'HeligentAdmin'}
            self.assertIn(client.get('/api/capability-mappings').status_code,(401,403))
            with patch('adsb_ingest.webapp.CapabilityMappingStore') as store:
                store.return_value.search.return_value = {'rows':[]}
                store.return_value.save.return_value = {'id':1}
                store.return_value.save_shared.return_value = {'id':2}
                self.assertEqual(client.get('/api/capability-mappings',headers=headers).status_code,200)
                self.assertEqual(client.post('/api/capability-mappings/1',headers=headers,json={}).status_code,403)
                self.assertEqual(client.post('/api/capability-mappings/1/shared',headers=headers,json={}).status_code,403)
                headers['X-Heligent-Auth-Email'] = 'analyst@example.com'
                no_csrf = {k:v for k,v in headers.items() if k != 'X-Requested-With'}
                self.assertEqual(client.post('/api/capability-mappings/1',headers=no_csrf,json={}).status_code,403)
                self.assertEqual(client.post('/api/capability-mappings/1/shared',headers=no_csrf,json={}).status_code,403)
                self.assertEqual(client.post('/api/capability-mappings/1',headers=headers,json={}).status_code,200)
                self.assertEqual(store.return_value.save.call_args.args[-1],'analyst@example.com')
                self.assertEqual(client.post('/api/capability-mappings/1/shared',headers=headers,json={}).status_code,200)
                self.assertEqual(store.return_value.save_shared.call_args.args[-1],'analyst@example.com')
        demo = create_app(start_worker=False,store=FakeAdminStore(),analytics_store=FakeAnalyticsStore(),natural_language=FakeNaturalLanguage(),public_demo=True)
        self.assertEqual(demo.test_client().get('/api/capability-mappings').status_code,404)

    def test_validation_requires_evidence_and_explicit_scope(self):
        p = dict(aircraft_type_code='ec45',match_level='POSSIBLE_FAMILY',notes='Family only',source_url='https://example.test/scope',revision=0)
        self.assertEqual(mapping_input(p)[0], 'EC45')
        for changes in [dict(notes=''),dict(source_url='javascript:alert(1)'),dict(source_url='https://user:pass@example.test'),dict(revision=True),dict(active='yes'),dict(aircraft_type_code='EC45/EC35'),dict(match_level='REVIEWED_TYPE')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError): mapping_input({**p,**changes})
        mapping_input({**p,'match_level':'REVIEWED_TYPE','type_wide_confirmed':True})
        mapping_input({**p,'match_level':'REVIEWED_TYPE','variant_scope':'C-2 only'})

    def test_mapping_scope_and_staleness(self):
        cap = dict(approval_status='VALID',capability_kind='AIRCRAFT',company_site_id=1)
        m = dict(active=True,aircraft_type_code='EC45',stale=False,match_level='POSSIBLE_FAMILY',variant_scope='')
        def match(**changes):
            return capability_match({**cap,'mappings':[{**m,**changes}]},'EC45',date(2026,9,7))
        self.assertEqual(match(), 'POSSIBLE_FAMILY')
        self.assertEqual(match(match_level='REVIEWED_TYPE'), 'SITE_MATCH')
        self.assertEqual(match(match_level='REVIEWED_TYPE',variant_scope='C-2'), 'POSSIBLE_FAMILY')
        self.assertEqual(match(stale=True), 'STALE_MAPPING')
        self.assertEqual(match(active=False), 'NO_RECORDED_MATCH')
        self.assertEqual(match(aircraft_type_code='EC35'), 'NO_RECORDED_MATCH')
        cap['company_site_id'] = None
        self.assertEqual(match(match_level='REVIEWED_TYPE'), 'COMPANY_MATCH')
        cap['approval_status'] = 'SUSPENDED'
        self.assertEqual(match(), 'APPROVAL_NOT_CURRENT')
