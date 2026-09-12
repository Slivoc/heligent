import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from adsb_ingest.aircraft_lookup import fetch_aircraft, lookup_aircraft, normalize_response
from adsb_ingest.source_tools import freshness, save_source_settings
from adsb_ingest.webapp import create_app
from test_webapp import FakeAdminStore, FakeAnalyticsStore, FakeNaturalLanguage, FakeAccessStore


class SourceToolsTests(unittest.TestCase):
    def test_freshness_uses_source_date_and_handles_unknown_and_future(self):
        today = date(2026, 9, 12)
        self.assertEqual(freshness(None, 7, today=today)['status'], 'NEVER')
        self.assertEqual(freshness(None, 7, has_evidence=True, today=today)['status'], 'UNKNOWN')
        self.assertEqual(freshness(date(2026, 9, 5), 7, today=today)['status'], 'CURRENT')
        self.assertEqual(freshness(date(2026, 9, 4), 7, today=today)['status'], 'STALE')
        self.assertEqual(freshness(date(2026, 9, 13), 7, today=today)['status'], 'FUTURE')

    def test_lookup_preserves_partial_results_without_inventing_type(self):
        r = normalize_response('ADSBDB', '4082a2', {'response': {'aircraft': {
            'mode_s': '4082A2', 'registration': 'g-test', 'icao_type': '', 'registered_owner': 'Private name'}}})
        self.assertEqual(r['registration'], 'G-TEST')
        self.assertIsNone(r['type_code'])
        self.assertNotIn('registered_owner', r)
        h = normalize_response('HEXDB', '4082a2', {'ModeS': '4082A2', 'Registration': 'G-TEST', 'ICAOTypeCode': 'ec45'})
        self.assertEqual(h['type_code'], 'EC45')
        self.assertEqual(normalize_response('HEXDB', '4082a2', {'status': '404'})['status'], 'NOT_FOUND')
        self.assertEqual(normalize_response('ADSBDB', '4082a2', {'response': 'unknown aircraft'})['status'], 'NOT_FOUND')
        for payload in ({'response': []}, {'response': {'aircraft': {'mode_s': 'abcdef'}}}, []):
            with self.assertRaises(ValueError):
                normalize_response('ADSBDB', '4082a2', payload)

    def test_network_is_bounded_and_redirects_are_not_followed(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 302
        with patch('adsb_ingest.aircraft_lookup.requests.get', return_value=response) as get:
            self.assertEqual(fetch_aircraft('HEXDB', '4082a2')['status'], 'FAILED')
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
            response.status_code = 200
            response.iter_content.return_value = [b'x' * (129 * 1024)]
            self.assertEqual(fetch_aircraft('HEXDB', '4082a2')['status'], 'FAILED')
            response.status_code = 404
            self.assertEqual(fetch_aircraft('HEXDB', '4082a2')['status'], 'NOT_FOUND')

    def test_validate_before_database_or_network(self):
        for provider, address in [('OTHER', '4082a2'), ('HEXDB', '../bad'), ('ADSBDB', None), ('HEXDB', '~4082a2')]:
            with self.assertRaises(ValueError): lookup_aircraft(None, provider, address, 'test')
        for payload in ({'refresh_days': True}, {'refresh_days': 0}, {'refresh_days': 7, 'notes': 'x'*4001}, []):
            with self.assertRaises(ValueError): save_source_settings(None, 'HEXDB', payload, 'test')

    def test_endpoints_are_private_and_mutations_require_analyst(self):
        args = dict(start_worker=False, store=FakeAdminStore(), analytics_store=FakeAnalyticsStore(), natural_language=FakeNaturalLanguage())
        demo = create_app(**args, public_demo=True).test_client()
        for path in ('/api/tools/sources', '/api/tools/sources/HEXDB', '/api/tools/identity-gaps'):
            self.assertEqual(demo.get(path).status_code, 404)
        with patch.dict('os.environ', {'HELIGENT_AUTH_PROXY_SECRET': 's'*40, 'HELIGENT_BOOTSTRAP_ADMIN_EMAILS': ''}):
            client = create_app(**args, access_store=FakeAccessStore(), auth_mode='NGROK').test_client()
            headers = {'X-Heligent-Proxy-Secret': 's'*40, 'X-Heligent-Auth-Email': 'viewer@example.com', 'X-Requested-With': 'HeligentAdmin'}
            self.assertIn(client.get('/api/tools/sources').status_code, (401,403))
            with patch('adsb_ingest.source_tools.source_inventory', return_value={'sources': []}):
                self.assertEqual(client.get('/api/tools/sources', headers=headers).status_code, 200)
            for action in ('lookup', 'settings'):
                self.assertEqual(client.post('/api/tools/sources/HEXDB/'+action, headers=headers, json={'address':'4082a2'}).status_code, 403)
