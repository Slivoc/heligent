import gzip
import json
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_postgres_integration as fixtures
from test_tar1090 import TYPES, csv_blob
from adsb_ingest.source_tools import source_detail
from adsb_ingest.tar1090 import refresh_snapshot, build_preview, preview_page, review_identity


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'TEST_DATABASE_URL is not configured')
class Tar1090IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PostgresIntegrationTests.setUpClass.__func__(cls)
        types = {f'X{i:03d}':['Fixture','L1P','L'] for i in range(1000)} | TYPES
        rows = [[f'{0x100000+i:06X}','','','00','','','',''] for i in range(100000)]
        rows += [['F00001','G-PUMA','PUMA','00','Puma','','',''],
                 ['F00002','VEHICLE','GND','00','Ground vehicle','','',''],
                 ['F00003','G-WRONG','B738','00','737','','',''],
                 ['F00004','G-FIXED','B738','00','737','','','']]
        cls.download = (csv_blob(rows), gzip.compress(json.dumps(types).encode(),mtime=0),
                        dict(csv_revision='a'*40,types_revision='b'*40,
                             source_date='2026-09-01T00:00:00+00:00',types_date='2026-08-31T00:00:00+00:00',
                             source_url='https://example.test/aircraft.csv.gz',types_url='https://example.test/types.js'))

    def setUp(self):
        with self.store.connect() as c:
            c.execute('TRUNCATE tar1090_preview,tar1090_attempt,tar1090_snapshot CASCADE')
            c.execute("DELETE FROM aircraft_metadata_override WHERE address LIKE 'f000%'")
            c.execute("DELETE FROM aircraft_identity_review WHERE address LIKE 'f000%'")
            c.execute("DELETE FROM aircraft_type_classification WHERE classification_source='TAR1090_DB_REVIEW'")
            c.execute('TRUNCATE dataset_day CASCADE')
            c.execute("DELETE FROM aircraft WHERE address LIKE 'f000%'")
        catalog,discovery,base = fixtures.PostgresIntegrationTests.fixture(self)
        self.store.upsert_airports(catalog)
        self.dataset = self.store.upsert_dataset(discovery)
        job = self.store.start_job(self.dataset,'PROCESS')
        self.store.set_processing(self.dataset)
        summaries=[]
        for i in range(1,6):
            address=f'f0000{i}'
            summaries.append(replace(base,
                aircraft_day=replace(base.aircraft_day,address=address,registration='G-OLD' if i==3 else None,type_code=None,type_description=None),
                airport_presences=tuple(replace(r,address=address) for r in base.airport_presences),
                flight_segments=tuple(replace(r,address=address) for r in base.flight_segments),
                airport_visits=tuple(replace(r,address=address) for r in base.airport_visits)))
        self.store.load_summaries(self.dataset,job,iter(summaries))
        with patch('adsb_ingest.tar1090.download_snapshot',return_value=self.download):
            self.snapshot=refresh_snapshot(self.store,'test')

    def compare(self):
        return build_preview(self.store,{'from':'2026-08-20','to':'2026-08-20','region':'EU','gap':'EITHER'},'test')

    def assignment(self, preview):
        return dict(preview_id=preview['preview_id'],address='f00001',valid_from='2026-08-20',valid_to='2026-08-20',notes='Dated operator confirmation checked')

    def test_reference_is_idempotent_and_failure_preserves_evidence(self):
        with self.store.connect() as c:
            self.assertIsNone(c.execute("SELECT registration FROM aircraft_day WHERE address='f00001'").fetchone()[0])
            c.execute("UPDATE tar1090_attempt SET started_at=now()-interval '2 minutes'")
        with patch('adsb_ingest.tar1090.download_snapshot',return_value=self.download):
            again=refresh_snapshot(self.store,'test')
        self.assertEqual(again['status'],'UNCHANGED')
        self.assertEqual(again['snapshot_id'],self.snapshot['snapshot_id'])
        with self.store.connect() as c:
            c.execute("UPDATE tar1090_attempt SET started_at=now()-interval '2 minutes'")
        with patch('adsb_ingest.tar1090.download_snapshot',return_value=(b'broken',*self.download[1:])), self.assertRaises(ValueError):
            refresh_snapshot(self.store,'test')
        source=source_detail(self.store,'TAR1090_DB')
        self.assertTrue(source['latest_failed'])
        self.assertTrue(source['has_import'])
        self.assertEqual(str(source['snapshot_date']),'2026-09-01')
        self.assertEqual(source['field_coverage']['rows'],100004)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM tar1090_snapshot').fetchone()[0],1)

    def test_comparison_groups_candidates_and_preserves_unmatched(self):
        preview=self.compare()
        self.assertEqual(preview['total'],5)
        self.assertEqual(preview['matched'],4)
        self.assertEqual(preview['counts'],{'ROTORCRAFT':1,'GROUND_VEHICLE':1,'FIXED_WING':1,'CONFLICT':1,'UNMATCHED':1})
        self.assertEqual([r['address'] for r in preview['rows']],['f00001'])
        conflicts=preview_page(self.store,preview['preview_id'],{'group':'CONFLICT'})
        self.assertFalse(conflicts['rows'][0]['reviewable'])
        self.assertEqual(preview_page(self.store,preview['preview_id'],{'group':'ALL','search':'g-puma'})['filtered_count'],1)
        with self.store.connect() as c:
            c.execute("DELETE FROM aircraft_airport_visit WHERE address='f00005'")
        unlocated=build_preview(self.store,{'region':'UNLOCATED','gap':'TAIL'},'test')
        self.assertEqual(unlocated['total'],1)
        self.assertEqual(unlocated['counts'],{'UNMATCHED':1})

    def test_review_uses_retained_claim_and_audits_dated_assignment(self):
        preview=self.compare()
        payload=self.assignment(preview) | {'registration':'G-TAMPER','type_code':'B738','source_url':'https://wrong.test'}
        proposed=review_identity(self.store,payload,'test')
        self.assertEqual(proposed['assignment']['registration'],'G-PUMA')
        self.assertEqual(proposed['affected_days'],1)
        saved=review_identity(self.store,payload | {'token':proposed['token']},'test',save=True)
        self.assertTrue(saved['saved'])
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT registration,type_code,resolved_category::text FROM aircraft_day_identity WHERE address='f00001'").fetchone(),('G-PUMA','PUMA','ROTORCRAFT'))
            evidence=c.execute("SELECT assignment->'evidence' FROM aircraft_identity_review WHERE address='f00001'").fetchone()[0]
            self.assertEqual(evidence['sha256'],self.snapshot['metadata']['sha256'])
            self.assertEqual(evidence['preview_id'],preview['preview_id'])
        self.assertTrue(preview_page(self.store,preview['preview_id'],{})['rows'][0]['applied'])
        self.assertEqual(self.compare()['total'],4)
        with self.assertRaises(ValueError): review_identity(self.store,payload | {'token':proposed['token']},'test',save=True)

    def test_changed_records_and_registry_conflicts_block_apply(self):
        preview=self.compare()
        payload=self.assignment(preview)
        proposed=review_identity(self.store,payload,'test')
        with self.store.connect() as c:
            c.execute("UPDATE aircraft_day SET registration='G-OTHER' WHERE address='f00001'")
        with self.assertRaises(ValueError): review_identity(self.store,payload | {'token':proposed['token']},'test',save=True)
        with self.store.connect() as c:
            c.execute("UPDATE aircraft_day SET registration=NULL WHERE address='f00001'")
            batch=c.execute("""INSERT INTO aircraft_registry_import_batch(source_code,snapshot_date,downloaded_at,
                source_file_name,source_sha256,source_bytes,row_count) VALUES
                ('TC_CCAR','2026-08-20',now(),'tar-test.zip',%s,1,1) RETURNING id""",('f'*64,)).fetchone()[0]
            c.execute("""INSERT INTO aircraft_registry_record(import_batch_id,registration,address,manufacturer,model,
                registry_category,official_category,effective_date) VALUES
                (%s,'C-OTHER','f00001','AIRBUS','H145','HELICOPTER','ROTORCRAFT','2026-08-01')""",(batch,))
            c.execute('REFRESH MATERIALIZED VIEW aircraft_registry_resolved_cache')
        try:
            with self.assertRaisesRegex(ValueError,'registry'): review_identity(self.store,payload,'test')
        finally:
            with self.store.connect() as c:
                c.execute('DELETE FROM aircraft_registry_import_batch WHERE id=%s',(batch,))
                c.execute('REFRESH MATERIALIZED VIEW aircraft_registry_resolved_cache')

    def test_dates_notes_ground_and_conflicts_cannot_bypass_review(self):
        preview=self.compare()
        p=self.assignment(preview)
        for change in ({'valid_to':''},{'notes':''},{'address':'f00002'},{'address':'f00003'},
                       {'valid_from':'2026-09-01'},{'valid_to':'2026-08-19'},{'token':'forged'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                review_identity(self.store,p | change,'test',save=True)
