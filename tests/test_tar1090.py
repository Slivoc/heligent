import csv
import gzip
import io
import json
import unittest
from unittest.mock import MagicMock, patch

from adsb_ingest.tar1090 import aircraft_rows, type_reference, type_category, candidate, page, download_snapshot, expand


def csv_blob(rows):
    out = io.StringIO()
    writer = csv.writer(out, delimiter=';', escapechar='\\', quoting=csv.QUOTE_NONE, lineterminator='\n')
    writer.writerows(rows)
    return gzip.compress(out.getvalue().encode(), mtime=0)


TYPES = {'PUMA':['SA-330 Puma','H2T','L'], 'W3':['W-3','H2T','L'], 'B738':['737-800','L2J','M'],
         'GND':['','V0-','-'], 'V22':['Osprey','R2T','M'], 'GYRO':['','G0-','-'], 'GLID':['Glider','L0-','-']}


class Tar1090Tests(unittest.TestCase):
    def test_routes_require_private_analyst_and_csrf(self):
        from adsb_ingest.webapp import create_app
        from test_webapp import FakeAdminStore, FakeAnalyticsStore, FakeNaturalLanguage, FakeAccessStore
        args=dict(start_worker=False,store=FakeAdminStore(),analytics_store=FakeAnalyticsStore(),natural_language=FakeNaturalLanguage())
        demo=create_app(**args,public_demo=True).test_client()
        self.assertEqual(demo.get('/api/tools/tar1090/previews/test').status_code,404)
        with patch.dict('os.environ',{'HELIGENT_AUTH_PROXY_SECRET':'s'*40,'HELIGENT_BOOTSTRAP_ADMIN_EMAILS':''}):
            client=create_app(**args,access_store=FakeAccessStore(),auth_mode='NGROK').test_client()
            h={'X-Heligent-Proxy-Secret':'s'*40,'X-Heligent-Auth-Email':'viewer@example.com','X-Requested-With':'HeligentAdmin'}
            for path in ('refresh','preview','identity/preview','identity/assign'):
                self.assertEqual(client.post('/api/tools/tar1090/'+path,headers=h,json={}).status_code,403)
            h['X-Heligent-Auth-Email']='analyst@example.com'
            with patch('adsb_ingest.tar1090.refresh_snapshot',return_value={'status':'IMPORTED'}) as refresh:
                self.assertEqual(client.post('/api/tools/tar1090/refresh',headers={k:v for k,v in h.items() if k!='X-Requested-With'},json={}).status_code,403)
                refresh.assert_not_called()
                self.assertEqual(client.post('/api/tools/tar1090/refresh',headers=h,json={}).status_code,200)

    def test_real_csv_dialect_and_partial_claims(self):
        rows = list(aircraft_rows(csv_blob([['ABCDEF','G-TEST','PUMA','11000','Puma; "special"\\model','1999','Owner; name',''],
                                          ['123456','','','0010','','','','']])))
        self.assertEqual(rows[0]['description'], 'Puma; "special"\\model')
        self.assertEqual(rows[0]['flags'], '11000')  # Preserve unusual upstream flags as evidence.
        self.assertEqual(rows[0]['address'], 'abcdef')
        self.assertNotIn('owner', rows[0])
        self.assertIsNone(rows[1]['registration'])
        self.assertIsNone(rows[1]['type_code'])

    def test_corrupt_duplicate_truncated_and_oversize_sources_fail(self):
        valid = ['ABCDEF','G-TEST','PUMA','00','','','','']
        for blob in (b'bad', csv_blob([]), csv_blob([valid,valid]), csv_blob([valid[:-1]]),
                     csv_blob([['~12345',*valid[1:]]]), csv_blob([valid])[:-3]):
            with self.subTest(blob=blob[:12]), self.assertRaises(ValueError):
                list(aircraft_rows(blob))
        with self.assertRaises(ValueError): expand(gzip.compress(b'x'*101), 100)
        with patch('adsb_ingest.tar1090.MAX_ROWS', 1), self.assertRaises(ValueError):
            list(aircraft_rows(csv_blob([valid,['123456',*valid[1:]]])))

    def test_upstream_types_recover_helis_and_separate_ground_and_gliders(self):
        types = type_reference(gzip.compress(json.dumps(TYPES).encode()))
        for code in ('PUMA','W3','V22','GYRO'):
            self.assertEqual(type_category(code,types), 'ROTORCRAFT')
        self.assertEqual(type_category('GND',types), 'GROUND_VEHICLE')
        self.assertEqual(type_category('B738',types), 'FIXED_WING')
        self.assertEqual(type_category('GLID',types), 'OTHER')
        self.assertEqual(type_category('XXXX',types), 'UNKNOWN')
        for bad in ([], {}, {'EC45':['wrong']}, {'bad-code':['','','']}):
            with self.assertRaises(ValueError): type_reference(gzip.compress(json.dumps(bad).encode()))

    def test_candidates_keep_missing_matches_and_conflicts_visible(self):
        observed = dict(address='abcdef',registrations=[],types=[],categories=['UNKNOWN'],missing_tail=True,missing_type=True)
        claim = dict(registration='G-TEST',type_code='PUMA')
        row = candidate(observed,claim,TYPES,{})
        self.assertEqual(row['group'],'ROTORCRAFT')
        self.assertTrue(row['reviewable'])
        for change in (dict(registrations=['G-OTHER']),dict(types=['EC45']),dict(categories=['FIXED_WING'])):
            r=candidate({**observed,**change},claim,TYPES,{})
            self.assertEqual(r['group'],'CONFLICT')
            self.assertFalse(r['reviewable'])
        self.assertFalse(candidate(observed,claim,TYPES,{'PUMA':'FIXED_WING'})['reviewable'])
        self.assertFalse(candidate(observed,{**claim,'type_code':'GND'},TYPES,{})['reviewable'])
        self.assertEqual(candidate(observed,{**claim,'type_code':'GLID'},TYPES,{'GLID':'GLIDER'})['group'],'OTHER')
        self.assertEqual(candidate(observed,None,TYPES,{})['group'],'UNMATCHED')
        report={'rows':[dict(row,address=f'{i:06x}') for i in range(60)]}
        self.assertEqual(len(page(report,'test',{})['rows']),50)
        self.assertEqual(len(page(report,'test',{'offset':'50'})['rows']),10)
        self.assertEqual(page(report,'test',{'search':'G-TEST'})['filtered_count'],60)

    def test_download_pins_revisions_and_rejects_redirects(self):
        revision='a'*40
        response=MagicMock()
        response.__enter__.return_value=response
        response.status_code=200
        commit=json.dumps({'sha':revision,'commit':{'committer':{'date':'2026-09-01T00:00:00Z'}}}).encode()
        response.iter_content.side_effect=[[commit],[commit],[b'csv'],[b'types']]
        with patch('adsb_ingest.tar1090.requests.get',return_value=response) as get:
            a,t,meta=download_snapshot()
            self.assertEqual((a,t),(b'csv',b'types'))
            self.assertIn('/'+revision+'/aircraft.csv.gz',meta['source_url'])
            self.assertTrue(all(not call.kwargs['allow_redirects'] for call in get.call_args_list))
        response.status_code=302
        with patch('adsb_ingest.tar1090.requests.get',return_value=response), self.assertRaises(ValueError):
            download_snapshot()
