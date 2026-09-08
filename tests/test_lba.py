import unittest
from unittest.mock import patch
from adsb_ingest.lba import parse_directory, fetch_preview


def fixture():
    return '''<partial-response><changes><update id="tbliste"><![CDATA[
    <span class="ui-paginator-current">1-1 / 1</span>
    <div class="inhaber">Example &amp; Co</div><div class="gnr"><div>DE.145.TEST</div></div>
    <div class="strasse">Base road</div><div class="plz_ort">&nbsp;• 12345 City</div>
    <div class="rating">A3 (Base- und Line Maintenance)</div><div class="muster odd">EC135</div>
    <div class="rating">C6 - Equipment</div><div class="muster">Components only</div>
    <div class="strasse">Hospital</div><div class="plz_ort">Other city</div>
    <div class="rating">A3 (nur Line Maintenance)</div><div class="muster">MBB-BK117</div>
    ]]></update></changes></partial-response>'''


class LbaTests(unittest.TestCase):
    def test_import_confirmation_and_invalid_selection(self):
        from adsb_ingest.lba_import import import_selection
        for payload in [None,{}, {'confirmed':True,'preview_id':'not-a-uuid'}, {'confirmed':True,'preview_id':'00000000-0000-0000-0000-000000000001','organisation_index':True}]:
            with self.assertRaises(ValueError): import_selection(None,payload,'analyst@example.com')

    def test_import_role_and_csrf(self):
        from adsb_ingest.webapp import create_app
        from test_webapp import FakeAdminStore, FakeAnalyticsStore, FakeNaturalLanguage, FakeAccessStore
        args=dict(start_worker=False,store=FakeAdminStore(),analytics_store=FakeAnalyticsStore(),natural_language=FakeNaturalLanguage())
        with patch.dict('os.environ', {'HELIGENT_AUTH_PROXY_SECRET':'s'*40,'HELIGENT_BOOTSTRAP_ADMIN_EMAILS':''}):
            client=create_app(**args,access_store=FakeAccessStore(),auth_mode='NGROK').test_client()
            h={'X-Heligent-Proxy-Secret':'s'*40,'X-Heligent-Auth-Email':'viewer@example.com','X-Requested-With':'HeligentAdmin'}
            with patch('adsb_ingest.lba_import.import_selection',return_value={'batch_id':1}) as save:
                self.assertEqual(client.post('/api/tools/lba/import',headers=h,json={}).status_code,403)
                h['X-Heligent-Auth-Email']='analyst@example.com'
                self.assertEqual(client.post('/api/tools/lba/import',headers={k:v for k,v in h.items() if k!='X-Requested-With'},json={}).status_code,403)
                save.assert_not_called()
                self.assertEqual(client.post('/api/tools/lba/import',headers=h,json={}).status_code,200)
                self.assertEqual(save.call_args.args[-1],'analyst@example.com')
        demo=create_app(**args,public_demo=True).test_client()
        self.assertEqual(demo.get('/api/tools/lba/history').status_code,404)

    def test_preserves_site_rating_scope(self):
        p = parse_directory(fixture())
        o = p['organisations'][0]
        self.assertEqual(o['name'], 'Example & Co')
        self.assertEqual(o['sites'][0]['locality'], '12345 City')
        self.assertEqual(len(o['sites'][0]['ratings']), 2)
        self.assertEqual(o['sites'][1]['ratings'][0]['models'], ['MBB-BK117'])
        self.assertFalse(p['truncated'])
        self.assertTrue(parse_directory(fixture().replace('1-1 / 1','1-10 / 100'))['truncated'])

    def test_fail_closed_on_layout_or_scope_change(self):
        for raw in ['<html>login</html>',fixture().replace('1-1 / 1','?'),fixture().replace('class="strasse"','class="changed"')]:
            with self.assertRaises(ValueError): parse_directory(raw)
        for query in ['',None,'a','x'*101]:
            with self.assertRaises(ValueError): fetch_preview(query)

    def test_private_analyst_only_and_csrf(self):
        from adsb_ingest.webapp import create_app
        from test_webapp import FakeAdminStore, FakeAnalyticsStore, FakeNaturalLanguage, FakeAccessStore
        with patch.dict('os.environ', {'HELIGENT_AUTH_PROXY_SECRET':'s'*40,'HELIGENT_BOOTSTRAP_ADMIN_EMAILS':''}):
            app = create_app(start_worker=False,store=FakeAdminStore(),analytics_store=FakeAnalyticsStore(),natural_language=FakeNaturalLanguage(),access_store=FakeAccessStore(),auth_mode='NGROK')
            client = app.test_client()
            h = {'X-Heligent-Proxy-Secret':'s'*40,'X-Heligent-Auth-Email':'viewer@example.com','X-Requested-With':'HeligentAdmin'}
            with patch('adsb_ingest.lba.fetch_preview',return_value={'mode':'PREVIEW_ONLY'}) as fetch, patch('adsb_ingest.lba_import.stage_preview',return_value={'preview_id':'fixture'}):
                self.assertEqual(client.post('/api/tools/lba/preview',headers=h,json={'query':'ADAC'}).status_code,403)
                h['X-Heligent-Auth-Email']='analyst@example.com'
                self.assertEqual(client.post('/api/tools/lba/preview',headers={k:v for k,v in h.items() if k!='X-Requested-With'},json={'query':'ADAC'}).status_code,403)
                fetch.assert_not_called()
                self.assertEqual(client.post('/api/tools/lba/preview',headers=h,json={'query':'ADAC'}).status_code,200)
                fetch.assert_called_once_with('ADAC')
