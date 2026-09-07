import unittest
from datetime import date
from unittest.mock import patch

from adsb_ingest.maintenance_map import capability_match, date_window
from adsb_ingest.webapp import create_app
from test_webapp import FakeAdminStore, FakeAnalyticsStore, FakeNaturalLanguage, FakeAccessStore


class MapTests(unittest.TestCase):
    def test_date_range_is_bounded(self):
        self.assertEqual(date_window(None,None,date(2026,9,6)), (date(2026,8,31),date(2026,9,6)))
        for start,end in [('2026-08-01','2026-09-01'),('2026-09-02','2026-09-01'),('bad','2026-09-01')]:
            with self.assertRaises(ValueError): date_window(start,end,None)

    def test_match_never_promotes_company_scope_or_fuzzy_models(self):
        cap = dict(approval_status='VALID', aircraft_type_code='EC45', capability_kind='AIRCRAFT', company_site_id=1)
        today = date(2026,9,6)
        self.assertEqual(capability_match(cap,'ec45',today),'SITE_MATCH')
        self.assertEqual(capability_match({**cap,'company_site_id':None},'EC45',today),'COMPANY_MATCH')
        for change in [dict(approval_status='SUSPENDED'),dict(valid_to=date(2026,9,5)),dict(valid_from=date(2026,9,7))]:
            self.assertEqual(capability_match({**cap,**change},'EC45',today),'APPROVAL_NOT_CURRENT')
        for change in [dict(aircraft_type_code='EC45/EC35'),dict(capability_kind='COMPONENT'),dict(aircraft_type_code=None,model='EC45')]:
            self.assertEqual(capability_match({**cap,**change},'EC45',today),'NO_RECORDED_MATCH')
        self.assertEqual(capability_match(cap,None,today),'NO_RECORDED_MATCH')

    def test_routes_are_private_and_carto_is_image_only(self):
        app = create_app(start_worker=False, store=FakeAdminStore(), analytics_store=FakeAnalyticsStore(),
                         natural_language=FakeNaturalLanguage(), public_demo=True)
        for path in ['map-config','watches/1/map']:
            self.assertEqual(app.test_client().get('/api/maintenance/'+path).status_code,404)
        with patch.dict('os.environ', {'HELIGENT_AUTH_PROXY_SECRET':'s'*40,'HELIGENT_BOOTSTRAP_ADMIN_EMAILS':'','HELIGENT_CARTO_BASEMAP_KEY':'tile-key'}):
            app = create_app(start_worker=False, store=FakeAdminStore(), analytics_store=FakeAnalyticsStore(),
                             natural_language=FakeNaturalLanguage(), access_store=FakeAccessStore(), auth_mode='NGROK')
            client = app.test_client()
            self.assertIn(client.get('/api/maintenance/map-config').status_code, (401,403))
            headers = {'X-Heligent-Proxy-Secret':'s'*40,'X-Heligent-Auth-Email':'viewer@example.com'}
            result = client.get('/api/maintenance/map-config',headers=headers)
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json,{'carto_key':'tile-key'})
            self.assertIn("img-src 'self' data: https://basemaps.cartocdn.com;",result.headers['Content-Security-Policy'])
            with patch('adsb_ingest.maintenance_map.map_data', return_value={'stops':[]}) as read:
                self.assertEqual(client.get('/api/maintenance/watches/1/map?from=2026-08-01&to=2026-08-07',headers=headers).status_code,200)
                self.assertEqual(read.call_args.args[1:],(1,'2026-08-01','2026-08-07'))
