import unittest
from unittest.mock import patch
from adsb_ingest.webapp import create_app
from test_webapp import FakeAdminStore, FakeAnalyticsStore, FakeNaturalLanguage, FakeAccessStore


class UnidentifiedTests(unittest.TestCase):
    def test_private_read_only_route(self):
        args=dict(start_worker=False,store=FakeAdminStore(),analytics_store=FakeAnalyticsStore(),natural_language=FakeNaturalLanguage())
        demo=create_app(**args,public_demo=True)
        self.assertEqual(demo.test_client().get('/api/tools/unidentified').status_code,404)
        with patch.dict('os.environ',{'HELIGENT_AUTH_PROXY_SECRET':'s'*40,'HELIGENT_BOOTSTRAP_ADMIN_EMAILS':''}):
            client=create_app(**args,access_store=FakeAccessStore(),auth_mode='NGROK').test_client()
            self.assertIn(client.get('/api/tools/unidentified').status_code,(401,403))
            headers={'X-Heligent-Proxy-Secret':'s'*40,'X-Heligent-Auth-Email':'viewer@example.com'}
            with patch('adsb_ingest.unidentified.unidentified_activity',return_value={'rows':[]}) as read:
                self.assertEqual(client.get('/api/tools/unidentified?from=2026-08-01&to=2026-08-07',headers=headers).status_code,200)
                self.assertEqual(read.call_args.args[1:],('2026-08-01','2026-08-07',0))
                self.assertEqual(client.get('/api/tools/unidentified?offset=bad',headers=headers).status_code,400)
