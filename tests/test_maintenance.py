import unittest
from unittest.mock import patch

from adsb_ingest.maintenance import event_input, registration
from adsb_ingest.webapp import create_app
from test_webapp import FakeAdminStore, FakeAnalyticsStore, FakeNaturalLanguage, FakeAccessStore


class MaintenanceTests(unittest.TestCase):
    def test_registration_validation(self):
        self.assertEqual(registration(' g-abcd '), 'GABCD')
        for value in ['', None, '../../etc', 'a'*13]:
            with self.assertRaises(ValueError):
                registration(value)

    def test_evidence_and_dates_required(self):
        valid = dict(started_at='2025-01-01T00:00:00Z', ended_at='2025-01-02T00:00:00Z',
                     maintenance_kind='100 hour check', status='CONFIRMED', notes='Operator confirmation')
        self.assertEqual(event_input(valid)[3], 'CONFIRMED')
        for change in [dict(notes=''), dict(status='DUE'), dict(ended_at='2024-01-01T00:00:00Z'),
                       dict(started_at='2025-01-01'), dict(ended_at='2099-01-01T00:00:00Z')]:
            with self.assertRaises(ValueError):
                event_input({**valid, **change})

    def test_review_requires_analyst_and_csrf_header(self):
        with patch.dict('os.environ', {'HELIGENT_AUTH_PROXY_SECRET': 's'*40, 'HELIGENT_BOOTSTRAP_ADMIN_EMAILS': ''}):
            app = create_app(start_worker=False, store=FakeAdminStore(), analytics_store=FakeAnalyticsStore(),
                             natural_language=FakeNaturalLanguage(), access_store=FakeAccessStore(), auth_mode='NGROK')
        client = app.test_client()
        headers = {'X-Heligent-Proxy-Secret': 's'*40, 'X-Heligent-Auth-Email': 'viewer@example.com', 'X-Requested-With':'HeligentAdmin'}
        with patch('adsb_ingest.webapp.MaintenanceStore') as store:
            self.assertEqual(client.post('/api/maintenance/watches', json={'registration':'GTEST'}, headers=headers).status_code,403)
            self.assertEqual(client.post('/api/maintenance/watches/1/reviews', json={}, headers=headers).status_code,403)
            store.assert_not_called()

    def test_public_demo_cannot_read_watchlists(self):
        app = create_app(start_worker=False, store=FakeAdminStore(), analytics_store=FakeAnalyticsStore(),
                         natural_language=FakeNaturalLanguage(), public_demo=True)
        self.assertEqual(app.test_client().get('/api/maintenance/watches').status_code,404)
