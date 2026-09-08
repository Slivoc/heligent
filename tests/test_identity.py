import unittest
from unittest.mock import patch
from adsb_ingest.identity import validate
from adsb_ingest.webapp import create_app
from test_webapp import FakeAdminStore,FakeAnalyticsStore,FakeNaturalLanguage,FakeAccessStore

class IdentityTests(unittest.TestCase):
    def test_validation(self):
        p=dict(address='4082A2',registration='g-wsas',valid_from='2026-08-14',source_url='https://example.test/evidence',notes='Checked')
        self.assertEqual(validate(p)['registration'],'G-WSAS')
        for change in [dict(address='~4082a2'),dict(registration=''),dict(valid_to='2020-01-01'),dict(source_url='javascript:alert(1)'),dict(notes=''),dict(type_code='EC45/EC35')]:
            with self.assertRaises(ValueError): validate({**p,**change})

    def test_mutations_require_analyst_and_csrf(self):
        args=dict(start_worker=False,store=FakeAdminStore(),analytics_store=FakeAnalyticsStore(),natural_language=FakeNaturalLanguage())
        with patch.dict('os.environ',{'HELIGENT_AUTH_PROXY_SECRET':'s'*40,'HELIGENT_BOOTSTRAP_ADMIN_EMAILS':''}):
            client=create_app(**args,access_store=FakeAccessStore(),auth_mode='NGROK').test_client()
            h={'X-Heligent-Proxy-Secret':'s'*40,'X-Heligent-Auth-Email':'viewer@example.com','X-Requested-With':'HeligentAdmin'}
            with patch('adsb_ingest.identity.assign_identity',return_value={'saved':True}) as assign:
                for endpoint in ('preview','assign'):
                    self.assertEqual(client.post('/api/tools/identity/'+endpoint,headers=h,json={}).status_code,403)
                assign.assert_not_called()
                h['X-Heligent-Auth-Email']='analyst@example.com'
                self.assertEqual(client.post('/api/tools/identity/assign',headers={k:v for k,v in h.items() if k!='X-Requested-With'},json={}).status_code,403)
                self.assertEqual(client.post('/api/tools/identity/assign',headers=h,json={}).status_code,200)
                self.assertTrue(assign.call_args.kwargs['save'])
