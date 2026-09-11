from __future__ import annotations

import os
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg

from adsb_ingest.adsblol import DayReleases, Release, ReleaseAsset
from adsb_ingest.access import AccessStore, AccessUser
from adsb_ingest.analytics import AnalyticsStore
from adsb_ingest.companies import CompanyStore, parse_company_row
from adsb_ingest.intelligence_api import IntelligenceService
from adsb_ingest.aircraft_registry import AircraftRegistryStore, CasaAircraftRecord
from adsb_ingest.airports import Airport, AirportCatalog, AirportIndex
from adsb_ingest.archive import TracePayload
from adsb_ingest.summarize import summarize_trace
from adsb_ingest.work_queue import AdminStore, SequentialIngestionWorker
from adsb_ingest.maintenance import MaintenanceStore
from adsb_ingest.maintenance_map import map_data
from adsb_ingest.summarize import DERIVATION_VERSION
from test_phase2 import trace_row


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL is not configured")
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_DATABASE_URL is not None
        cls.store = AdminStore(TEST_DATABASE_URL)
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as connection:
            connection.execute("DROP SCHEMA public CASCADE")
            connection.execute("CREATE SCHEMA public")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase2.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase8.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase9.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase10.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase11.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase12.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase15.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase14.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase16.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase17.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase17.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase18.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase19.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase20.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase21.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase21.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase20.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase19.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase18.sql")
        # Application startup replays every idempotent migration. Phase 9 adds
        # company columns, so Phase 8 views must remain stable on the next run.
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase8.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase9.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase10.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase11.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase12.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase15.sql")
        cls.store.apply_schema(Path(__file__).parents[1] / "schema" / "phase14.sql")

    def test_maintenance_review_survives_archive_and_revision(self):
        pulse = MaintenanceStore(self.store)
        watch = pulse.add({'registration': 'G-PULSE'}, 'analyst@example.com')
        self.assertEqual(watch['id'], pulse.add({'registration': 'gpulse'}, 'analyst@example.com')['id'])
        payload = dict(started_at='2025-01-01T00:00:00Z', ended_at='2025-01-02T00:00:00Z',
                       maintenance_kind='100 hour check', status='CONFIRMED', notes='Operator confirmation')
        event = pulse.review(watch['id'], payload, 'analyst@example.com')
        revised = pulse.review(watch['id'], {**payload, 'event_id':event['id'], 'status':'UNCERTAIN'}, 'reviewer@example.com')
        self.assertEqual(revised['id'], event['id'])
        with self.store.connect() as c:
            snapshot = c.execute('SELECT snapshot FROM maintenance_event_revision WHERE event_id=%s', (event['id'],)).fetchone()[0]
        self.assertEqual(snapshot['status'], 'CONFIRMED')
        pulse.archive(watch['id'])
        self.assertNotIn(watch['id'], [w['id'] for w in pulse.watches()])
        self.assertEqual(len(pulse.detail(watch['id'])['events']), 1)
        self.assertEqual(pulse.add({'registration':'G-PULSE'}, 'analyst@example.com')['id'], watch['id'])

    def test_maintenance_watchlist_operator_metadata_and_restore(self):
        catalog, discovery, summary = self.fixture()
        self.store.upsert_airports(catalog)
        dataset_id = self.store.upsert_dataset(discovery)
        job = self.store.start_job(dataset_id, 'PROCESS')
        self.store.set_processing(dataset_id)
        self.store.load_summaries(dataset_id, job, iter([summary]))
        pulse = MaintenanceStore(self.store)
        watch = pulse.add({'registration': 'g-test', 'notes': 'Keep this team note'}, 'pulse@example.com')
        unknown = pulse.add({'registration': 'G-NODATA'}, 'pulse@example.com')
        assigned = pulse.add({'registration': 'G-UNSEEN'}, 'pulse@example.com')
        with self.store.connect() as c:
            c.execute("INSERT INTO company_data_source(code,name) VALUES ('PULSE_TEST','Pulse test')")
            operator = c.execute("INSERT INTO company(company_key,name,is_operator) VALUES ('pulse-operator','Pulse Air',true) RETURNING id").fetchone()[0]
            fallback = c.execute("INSERT INTO company(company_key,name,is_operator) VALUES ('pulse-fallback','Registration Air',true) RETURNING id").fetchone()[0]
            c.execute('''INSERT INTO company_aircraft_assignment(company_id,aircraft_address,source_code,external_id,confidence)
                VALUES (%s,'abcdef','PULSE_TEST','address',0.8)''', (operator,))
            c.execute('''INSERT INTO company_aircraft_assignment(company_id,registration,source_code,external_id,confidence)
                VALUES (%s,'G-TEST','PULSE_TEST','tail',1),(%s,'G-UNSEEN','PULSE_TEST','unseen',1)''', (fallback, fallback))
            # An archived/expired assignment and an owner must not become the operator.
            c.execute('''INSERT INTO company_aircraft_assignment(company_id,registration,source_code,external_id,valid_to,assignment_role)
                VALUES (%s,'G-NODATA','PULSE_TEST','expired','2000-01-01','OPERATOR'),
                       (%s,'G-NODATA','PULSE_TEST','owner',NULL,'OWNER')''', (operator, operator))
            other_list = c.execute("INSERT INTO maintenance_watchlist(name,scope_key) VALUES ('Other team','pulse-other') RETURNING id").fetchone()[0]
            c.execute("INSERT INTO maintenance_watch(watchlist_id,registration,created_by) VALUES (%s,'GPRIVATE','other@example.com')", (other_list,))
        rows = {row['id']: row for row in pulse.watches()}
        self.assertEqual(rows[watch['id']]['operator'], 'Pulse Air')
        self.assertEqual(rows[watch['id']]['type_code'], 'H145')
        self.assertEqual(rows[watch['id']]['last_seen_date'], date(2026, 8, 20))
        self.assertEqual(rows[assigned['id']]['operator'], 'Registration Air')
        self.assertIsNone(rows[assigned['id']]['type_code'])
        self.assertIsNone(rows[unknown['id']]['operator'])
        self.assertIsNone(rows[unknown['id']]['last_seen_date'])
        self.assertNotIn('GPRIVATE', [row['registration'] for row in rows.values()])
        event = pulse.review(watch['id'], dict(started_at='2025-01-01T00:00:00Z', ended_at='2025-01-02T00:00:00Z',
            maintenance_kind='100 hour check', status='CONFIRMED', notes='Operator confirmation'), 'pulse@example.com')
        pulse.archive(watch['id'])
        self.assertNotIn(watch['id'], [row['id'] for row in pulse.watches()])
        restored = pulse.add({'registration': watch['registration'], 'notes': watch['notes']}, 'pulse@example.com')
        self.assertEqual(restored['id'], watch['id'])
        self.assertEqual(restored['notes'], 'Keep this team note')
        restored_row = next(row for row in pulse.watches() if row['id'] == watch['id'])
        self.assertEqual(restored_row['last_confirmed_maintenance'], event['ended_at'])
        self.assertIn(event['id'], [row['id'] for row in pulse.detail(watch['id'])['events']])
        # No completed observation data should still leave every watch visible.
        with self.store.connect() as c:
            c.execute("UPDATE dataset_day SET status='PROCESSING' WHERE id=%s", (dataset_id,))
        row = next(row for row in pulse.watches() if row['id'] == watch['id'])
        self.assertIsNone(row['type_code'])
        self.assertIsNone(row['last_seen_date'])
        self.assertEqual(row['operator'], 'Registration Air')
        with self.store.connect() as c:
            c.execute("UPDATE dataset_day SET status='PROCESSED' WHERE id=%s", (dataset_id,))

    def fixture(self):
        airport = Airport(
            ident="TEST",
            airport_type="small_airport",
            name="Test Airport",
            latitude_deg=51.0,
            longitude_deg=-1.0,
            elevation_ft=100,
            continent="EU",
            iso_country="GB",
            iso_region="GB-ENG",
            municipality="Test",
            scheduled_service=False,
            gps_code="EGTT",
            iata_code=None,
            local_code=None,
        )
        catalog = AirportCatalog(
            airports=(airport,),
            source_url="https://example.test/airports.csv",
            sha256="0" * 64,
            downloaded_at=datetime.now(UTC),
        )
        release = Release(
            tag="v2026.08.20-planes-readsb-test-0",
            html_url="https://example.test/release",
            published_at="2026-08-21T00:00:00Z",
            assets=(ReleaseAsset("test.tar.aa", 100, "https://example.test/test.tar.aa"),),
        )
        discovery = DayReleases(
            utc_date=date(2026, 8, 20),
            repository="example/test",
            preferred=release,
            variants=(release,),
        )
        payload = {
            "icao": "abcdef",
            "r": "G-TEST",
            "t": "H145",
            "desc": "AIRBUS HELICOPTERS H145",
            "version": "readsb test",
            "timestamp": 1787184000.0,
            "trace": [
                trace_row(0, 51.0, -1.0, "ground", 0, flight="TEST1"),
                trace_row(30, 51.001, -1.0, "ground", 10, flight="TEST1"),
                trace_row(60, 51.01, -1.0, 500, 100, flight="TEST1"),
                trace_row(90, 51.1, -1.0, 1_500, 120, flight="TEST1"),
            ],
        }
        trace = TracePayload("trace.json", "abcdef", 100, 500, payload)
        summary = summarize_trace(trace, discovery.utc_date, AirportIndex([airport]))
        return catalog, discovery, summary

    def test_map_existing_stops_and_capability_scope(self):
        catalog, discovery, summary = self.fixture()
        self.store.upsert_airports(catalog)
        dataset_id = self.store.upsert_dataset(discovery)
        job = self.store.start_job(dataset_id, 'PROCESS')
        self.store.set_processing(dataset_id)
        self.store.load_summaries(dataset_id, job, iter([summary]))
        watch = MaintenanceStore(self.store).add({'registration':'G-TEST'},'map@example.com')
        with self.store.connect() as c:
            company = c.execute("INSERT INTO company(company_key,name,is_mro) VALUES ('map-test','Map MRO',true) RETURNING id").fetchone()[0]
            first = c.execute("INSERT INTO company_site(company_id,site_key,name,airport_ident) VALUES (%s,'base-a','Base A','TEST') RETURNING id",(company,)).fetchone()[0]
            second = c.execute("INSERT INTO company_site(company_id,site_key,name) VALUES (%s,'base-b','Base B') RETURNING id",(company,)).fetchone()[0]
            approval = c.execute("INSERT INTO regulatory_approval(company_id,authority_code,approval_type,approval_number) VALUES (%s,'TEST','PART_145','MAP.145') RETURNING id",(company,)).fetchone()[0]
            c.execute("INSERT INTO company_data_source(code,name) VALUES ('MAP_TEST','Map test')")
            c.execute('''INSERT INTO approval_capability(regulatory_approval_id,company_site_id,source_code,capability_key,capability_kind,aircraft_type_code)
                VALUES (%s,%s,'MAP_TEST','site','AIRCRAFT','H145'),(%s,NULL,'MAP_TEST','company','AIRCRAFT','H145')''',(approval,first,approval))
        data = map_data(self.store,watch['id'],'2026-08-20','2026-08-20')
        self.assertEqual(data['type_code'],'H145')
        self.assertNotIn('flights', data)
        self.assertEqual(data['stops'][0]['airport_ident'],'TEST')
        self.assertEqual(data['stops'][0]['number'],1)
        self.assertGreaterEqual(data['stops'][0]['evidence_span_seconds'], data['stops'][0]['ground_time_seconds'])
        a = next(s for s in data['sites'] if s['id']==first)
        b = next(s for s in data['sites'] if s['id']==second)
        self.assertEqual(a['match'],'SITE_MATCH')
        self.assertEqual(a['location_precision'],'AIRPORT_CENTROID')
        self.assertEqual(b['match'],'COMPANY_MATCH')
        self.assertEqual(b['location_precision'],'UNLOCATED')
        self.assertEqual(len(b['capabilities']),1) # no other site's capability leaks
        # Exercise the actual map query path, not only the pure matcher.
        from adsb_ingest.capability_mappings import CapabilityMappingStore
        reviews = CapabilityMappingStore(self.store)
        with self.store.connect() as c:
            c.execute("UPDATE approval_capability SET aircraft_type_code=NULL,model='Map test shared aircraft' WHERE regulatory_approval_id=%s", (approval,))
            cap_id = c.execute("SELECT id FROM approval_capability WHERE regulatory_approval_id=%s AND company_site_id=%s", (approval,first)).fetchone()[0]
        payload = dict(aircraft_type_code='H145',match_level='REVIEWED_TYPE',type_wide_confirmed=True,
            source_url='https://example.test/map-scope',notes='Shared map test',revision=0,
            shared_confirmed=True,evidence_snapshot=reviews.detail(cap_id)['source_snapshot'])
        reviews.save_shared(cap_id, payload, 'map@example.com')
        mapped = map_data(self.store,watch['id'],'2026-08-20','2026-08-20')
        a = next(s for s in mapped['sites'] if s['id']==first)
        b = next(s for s in mapped['sites'] if s['id']==second)
        self.assertEqual(a['match'], 'SITE_MATCH')
        self.assertEqual(b['match'], 'COMPANY_MATCH')
        self.assertTrue(all(cap['mappings'][0]['origin']=='SHARED' for cap in a['capabilities']))
        reviews.save(cap_id, {**payload,'active':False}, 'map@example.com')
        mapped = map_data(self.store,watch['id'],'2026-08-20','2026-08-20')
        a = next(s for s in mapped['sites'] if s['id']==first)
        site_cap = next(cap for cap in a['capabilities'] if cap['capability_id']==cap_id)
        self.assertEqual(site_cap['match'], 'NO_RECORDED_MATCH')
        self.assertEqual(site_cap['mappings'][0]['origin'], 'LOCAL')
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='aircraft_flight_segment' AND column_name='track'").fetchone()[0],0)
        empty = MaintenanceStore(self.store).add({'registration':'G-EMPTY'},'map@example.com')
        self.assertEqual(map_data(self.store,empty['id'],'2026-08-20','2026-08-20')['stops'],[])
        with self.assertRaises(ValueError):
            map_data(self.store,999999,'2026-08-20','2026-08-20')

    def test_lba_selected_import_is_atomic_and_repeatable(self):
        from adsb_ingest.lba_import import stage_preview,import_selection,import_history
        org={'name':'LBA Test Helicopters','approval':'DE.145.TESTLBA - gültig seit 01.09.2026','sites':[
            {'street':'Hangar 1','locality':'12345 Test town','ratings':[{'wording':'A3 - Hubschrauber (Base- und Line Maintenance)','models':['MBB-BK117']}]},
            {'street':'Hospital 2','locality':'23456 Other town','ratings':[{'wording':'A3 - Hubschrauber (nur Line Maintenance)','models':['EC135']}, {'wording':'C6 - Ausrüstung','models':['Components']}]}]}
        snapshot={'organisations':[{'name':'Do not import'},org],'truncated':False,'fetched_at':'2026-09-08T12:00:00Z','source_sha256':'a'*64}
        staged=stage_preview(self.store,snapshot,'analyst@example.com')
        payload={'preview_id':staged['preview_id'],'organisation_index':1,'confirmed':True}
        result=import_selection(self.store,payload,'analyst@example.com')
        self.assertEqual(result['sites'],2)
        self.assertEqual(result['capabilities_added'],3)
        self.assertFalse(result['already_imported'])
        self.assertTrue(import_selection(self.store,payload,'analyst@example.com')['already_imported'])
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM company WHERE name='Do not import'").fetchone()[0],0)
            caps=c.execute("SELECT capability_kind,is_base_maintenance,is_line_maintenance,aircraft_type_code FROM approval_capability WHERE regulatory_approval_id=%s ORDER BY id",(result['approval_id'],)).fetchall()
            self.assertEqual(caps[0],('AIRCRAFT',True,True,None))
            self.assertEqual(caps[1],('AIRCRAFT',False,True,None))
            self.assertEqual(caps[2][0],'COMPONENT')
            c.execute("UPDATE company_site SET name='Curated hangar' WHERE company_id=%s",(result['company_id'],))
        fresh=stage_preview(self.store,snapshot,'analyst@example.com')
        self.assertTrue(import_selection(self.store,{**payload,'preview_id':fresh['preview_id']},'analyst@example.com')['already_imported'])
        org['sites'][0]['ratings'][0]['models'].append('EC135')
        changed=stage_preview(self.store,snapshot,'analyst@example.com')
        with self.assertRaises(ValueError): import_selection(self.store,{**payload,'preview_id':changed['preview_id']},'analyst@example.com')
        self.assertEqual(len(import_history(self.store)),1)
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT name FROM company_site WHERE company_id=%s LIMIT 1',(result['company_id'],)).fetchone()[0],'Curated hangar')
        # A malformed later site must not leave a company or partial approval behind.
        org['name']='Rejected LBA Organisation'
        org['approval']='DE.145.REJECTED - gültig seit 01.09.2026'
        org['sites'][1]['ratings']=[]
        broken=stage_preview(self.store,snapshot,'analyst@example.com')
        with self.assertRaises(ValueError): import_selection(self.store,{**payload,'preview_id':broken['preview_id']},'analyst@example.com')
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM company WHERE name='Rejected LBA Organisation'").fetchone()[0],0)

    def test_zzz_identity_assignment(self):
        from adsb_ingest.identity import assign_identity
        payload=dict(address='abcdef',registration='G-TEST',type_code='H145',valid_from='2026-08-20',valid_to='2026-08-20',source_url='https://example.test/registry',notes='Verified identity')
        with self.store.connect() as c:
            c.execute("UPDATE aircraft_day SET registration=NULL WHERE address='abcdef'")
        p=assign_identity(self.store,payload,'analyst@example.com')
        self.assertEqual(p['affected_days'],1)
        with self.assertRaises(ValueError): assign_identity(self.store,{**payload,'token':'old'},'analyst@example.com',True)
        result=assign_identity(self.store,{**payload,'token':p['token']},'analyst@example.com',True)
        self.assertTrue(result['saved'])
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT registration FROM aircraft_day WHERE address='abcdef'").fetchone()[0],'G-TEST')
            audit=c.execute("SELECT previous_rows,reviewed_by FROM aircraft_identity_review WHERE address='abcdef'").fetchone()
            self.assertIsNone(audit[0][0]['registration'])
            self.assertEqual(audit[1],'analyst@example.com')
        with self.assertRaises(ValueError): assign_identity(self.store,payload,'analyst@example.com')

    def test_unidentified_activity(self):
        from adsb_ingest.unidentified import unidentified_activity
        def restore_identity():
            with self.store.connect() as c:
                c.execute("UPDATE aircraft_day SET registration='G-TEST' WHERE address='abcdef'")
        self.addCleanup(restore_identity)
        catalog, discovery, summary = self.fixture()
        self.store.upsert_airports(catalog)
        dataset = self.store.upsert_dataset(discovery)
        job = self.store.start_job(dataset, 'PROCESS')
        self.store.set_processing(dataset)
        self.store.load_summaries(dataset, job, iter([summary]))
        with self.store.connect() as c:
            c.execute("UPDATE aircraft_day SET registration=' ' WHERE dataset_day_id=%s AND address='abcdef'", (dataset,))
        result = unidentified_activity(self.store, '2026-08-20', '2026-08-20')
        row = next(r for r in result['rows'] if r['address']=='abcdef')
        self.assertGreater(row['positions'], 0)
        self.assertEqual(row['days'], 1)
        self.assertIn('TEST1', row['callsigns'])
        self.assertEqual(row['current_registration'], 'G-TEST')
        self.assertEqual(row['top_visits'][0]['airport_ident'],'TEST')
        with self.store.connect() as c:
            c.execute("INSERT INTO aircraft_type_classification(type_code,category,classification_source) VALUES ('H145','ROTORCRAFT','TEST') ON CONFLICT(type_code) DO UPDATE SET category='ROTORCRAFT'")
        self.assertTrue(unidentified_activity(self.store,'2026-08-20','2026-08-20',category='ROTORCRAFT',region='EU')['rows'])
        self.assertFalse(unidentified_activity(self.store,'2026-08-20','2026-08-20',category='FIXED_WING')['rows'])
        self.assertFalse(unidentified_activity(self.store,'2026-08-20','2026-08-20',region='NA')['rows'])
        with self.store.connect() as c:
            c.execute("UPDATE aircraft_day SET type_code=NULL WHERE address='abcdef'")
        self.assertTrue(unidentified_activity(self.store,'2026-08-20','2026-08-20',category='ROTORCRAFT_UNKNOWN',region='EU')['rows'])
        self.assertFalse(unidentified_activity(self.store,'2026-08-20','2026-08-20',category='ROTORCRAFT')['rows'])
        with self.store.connect() as c:
            c.execute("UPDATE aircraft_day SET type_code='H145' WHERE address='abcdef'")
        self.assertFalse(result['has_more'])
        with self.assertRaises(ValueError): unidentified_activity(self.store,'2026-08-01','2026-09-05')

    def test_unidentified_untyped_search_preserves_days_and_visits(self):
        from adsb_ingest.unidentified import unidentified_activity
        catalog, discovery, _ = self.fixture()
        self.store.upsert_airports(catalog)
        datasets = []
        for day in (date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)):
            release = replace(discovery.preferred, tag=f'unidentified-{day}')
            dataset = self.store.upsert_dataset(replace(discovery, utc_date=day, preferred=release, variants=(release,)))
            datasets.append(dataset)
            summaries = []
            # Put the untyped GWSAS clue beyond the first page of hour-ranked results.
            for address in ['4082a2', '4082a1', '4082f0'] + [f'{0x100000+i:06x}' for i in range(51)]:
                callsign = ('GWSAS' if day.day != 2 else 'RESCUE1') if address == '4082a2' else 'GXSAS'
                payload = {'icao':address, 'r':None, 't':'B738' if address=='4082f0' else None,
                    'timestamp':datetime.combine(day, datetime.min.time(), UTC).timestamp(),
                    'trace':[trace_row(0,51,-1,'ground',0,flight=callsign),
                             trace_row(30,51.001,-1,'ground',10,flight=callsign),
                             trace_row(60,51.01,-1,500,100,flight=callsign),
                             trace_row(90,51.1,-1,1500,120,flight=callsign)]}
                summaries.append(summarize_trace(TracePayload('trace.json',address,100,500,payload),day,AirportIndex(catalog.airports)))
            job = self.store.start_job(dataset, 'PROCESS')
            self.store.set_processing(dataset)
            self.store.load_summaries(dataset, job, iter(summaries))
        with self.store.connect() as c:
            c.execute("INSERT INTO aircraft_type_classification(type_code,category,classification_source) VALUES ('B738','FIXED_WING','TEST') ON CONFLICT(type_code) DO UPDATE SET category='FIXED_WING'")
            c.execute("UPDATE dataset_day SET status='FAILED_PROCESSING' WHERE id=%s", (datasets[-1],))
        filters = dict(start='2026-09-01', end='2026-09-03', region='EU')
        first = unidentified_activity(self.store, **filters)
        self.assertTrue(first['has_more'])
        self.assertNotIn('4082a2', [r['address'] for r in first['rows']])
        second = unidentified_activity(self.store, **filters, offset=50)
        expected = next(r for r in second['rows'] if r['address']=='4082a2')
        self.assertNotIn('4082f0', [r['address'] for r in first['rows']+second['rows']])
        for search in ('G-WSAS', ' g wsas ', '4082A2', '82a2'):
            result = unidentified_activity(self.store, **filters, search=search)
            self.assertEqual(result['rows'], [expected])
            self.assertEqual(result['processed_days'], 2)
            self.assertFalse(result['has_more'])
            self.assertEqual(expected['days'], 2)
            self.assertEqual(expected['types'], [])
            self.assertIn('RESCUE1', expected['callsigns'])
            self.assertEqual(expected['top_visits'][0]['airport_ident'], 'TEST')
        self.assertFalse(unidentified_activity(self.store, **filters, category='ROTORCRAFT')['rows'])
        self.assertFalse(unidentified_activity(self.store, **{**filters,'region':'NA'}, search='GWSAS')['rows'])
        self.assertFalse(unidentified_activity(self.store, **filters, search="GWSAS' OR 1=1 --")['rows'])

    def test_capability_shared_mapping_reuse_and_audit(self):
        from adsb_ingest.capability_mappings import CapabilityMappingStore, effective_mappings
        mappings = CapabilityMappingStore(self.store)
        with self.store.connect() as c:
            company = c.execute("INSERT INTO company(company_key,name) VALUES ('shared-review','Shared MRO') RETURNING id").fetchone()[0]
            approval = c.execute("INSERT INTO regulatory_approval(company_id,authority_code,approval_type,approval_number) VALUES (%s,'TEST','PART_145','SHARED.145') RETURNING id", (company,)).fetchone()[0]
            c.execute("INSERT INTO company_data_source(code,name) VALUES ('SHARED_TEST','Shared test')")
            caps = [c.execute("INSERT INTO approval_capability(regulatory_approval_id,source_code,capability_key,capability_kind,model,limitation,is_base_maintenance,is_line_maintenance) VALUES (%s,'SHARED_TEST',%s,'AIRCRAFT',%s,%s,%s,true) RETURNING id", (approval,str(i),phrase,scope,base)).fetchone()[0]
                    for i,phrase,scope,base in [(1,'Airbus EC135','Base and line',True),(2,'AIRBUS  EC135','Line only',False)]]
        first = mappings.detail(caps[0])
        payload = dict(aircraft_type_code='EC35', match_level='POSSIBLE_FAMILY', source_url='https://example.test/approval', notes='Shared phrase review', revision=0, active=True, evidence_snapshot=first['source_snapshot'], shared_confirmed=True)
        with self.assertRaises(ValueError): mappings.save_shared(caps[0], {**payload,'shared_confirmed':False}, 'analyst')
        saved = mappings.save_shared(caps[0], payload, 'analyst')
        second = mappings.detail(caps[1])
        self.assertEqual(second['shared_mappings'][0]['id'], saved['id'])
        self.assertFalse(second['capability']['is_base_maintenance'])
        self.assertEqual(mappings.search('Shared MRO')['rows'][0]['shared_mapping_count'], 1)
        with self.assertRaises(ValueError): mappings.save_shared(caps[1], {**payload,'evidence_snapshot':second['source_snapshot']}, 'other')
        withdrawn = mappings.save_shared(caps[1], {**payload,'revision':1,'active':False,'evidence_snapshot':second['source_snapshot']}, 'other')
        self.assertEqual(withdrawn['revision'], 2)
        self.assertEqual(mappings.detail(caps[0])['shared_history'][0]['snapshot']['reviewed_by'], 'analyst')
        mappings.save_shared(caps[0], {**payload,'revision':2}, 'analyst')
        mappings.save(caps[1], {**payload,'active':False,'evidence_snapshot':second['source_snapshot']}, 'analyst')
        second = mappings.detail(caps[1])
        chosen = effective_mappings(second['capability'], second['mappings'], second['shared_mappings'])
        self.assertEqual(chosen[0]['origin'], 'LOCAL')
        self.assertFalse(chosen[0]['active'])
        with self.store.connect() as c:
            c.execute("UPDATE approval_capability SET model='EC135 restricted' WHERE id=%s", (caps[0],))
        self.assertEqual(mappings.detail(caps[0])['shared_mappings'], [])
        with self.assertRaises(ValueError): mappings.save_shared(caps[0], {**payload,'revision':3}, 'analyst')

    def test_capability_mapping_audit_and_stale_scope(self):
        from adsb_ingest.capability_mappings import CapabilityMappingStore
        mappings = CapabilityMappingStore(self.store)
        with self.store.connect() as c:
            company = c.execute("INSERT INTO company(company_key,name) VALUES ('mapping-review','Review MRO') RETURNING id").fetchone()[0]
            approval = c.execute("INSERT INTO regulatory_approval(company_id,authority_code,approval_type,approval_number) VALUES (%s,'TEST','PART_145','REVIEW.145') RETURNING id", (company,)).fetchone()[0]
            c.execute("INSERT INTO company_data_source(code,name) VALUES ('REVIEW_TEST','Review test')")
            cap = c.execute("INSERT INTO approval_capability(regulatory_approval_id,source_code,capability_key,capability_kind,limitation) VALUES (%s,'REVIEW_TEST','family','AIRCRAFT','MBB-BK117 SERIES') RETURNING id", (approval,)).fetchone()[0]
        detail = mappings.detail(cap)
        payload = dict(aircraft_type_code='ec45', match_level='POSSIBLE_FAMILY', variant_scope='', source_url='https://example.test/approval', notes='Family relationship only', revision=0, active=True, evidence_snapshot=detail['source_snapshot'])
        saved = mappings.save(cap, payload, 'analyst@example.com')
        self.assertEqual(saved['aircraft_type_code'], 'EC45')
        self.assertEqual(mappings.search('Review MRO')['rows'][0]['mapping_count'], 1)
        with self.assertRaises(ValueError): mappings.save(cap, payload, 'second@example.com')
        revised = mappings.save(cap, {**payload, 'revision':1, 'active':False}, 'second@example.com')
        self.assertEqual(revised['revision'], 2)
        self.assertEqual(mappings.detail(cap)['history'][0]['snapshot']['reviewed_by'], 'analyst@example.com')
        with self.store.connect() as c:
            c.execute("UPDATE approval_capability SET limitation='C-2 only' WHERE id=%s", (cap,))
        self.assertTrue(mappings.detail(cap)['mappings'][0]['stale'])
        with self.assertRaises(ValueError): mappings.save(cap, {**payload,'revision':2}, 'analyst@example.com')
        fresh = mappings.detail(cap)['source_snapshot']
        mappings.save(cap, {**payload,'revision':2,'evidence_snapshot':fresh}, 'analyst@example.com')
        self.assertFalse(mappings.detail(cap)['mappings'][0]['stale'])

    def test_company_import_is_idempotent_and_preserves_roles_and_scope(self) -> None:
        assert TEST_DATABASE_URL is not None
        parsed = parse_company_row(
            {
                "company_name": "Integration Rotor Services Ltd",
                "company_key": "integration-rotor-services-gb",
                "source_company_id": "UK.145.INTEGRATION",
                "is_operator": "yes",
                "is_mro": "yes",
                "company_country_code": "GB",
                "site_name": "Integration Base",
                "source_site_id": "UK.145.INTEGRATION:BASE",
                "is_base_maintenance_site": "yes",
                "authority_code": "UK_CAA",
                "approval_type": "PART_145",
                "approval_number": "UK.145.INTEGRATION",
                "approval_status": "VALID",
                "capability_kind": "AIRCRAFT",
                "source_capability_id": "UK.145.INTEGRATION:H145",
                "manufacturer": "Airbus Helicopters",
                "model": "H145",
                "aircraft_type_code": "H145",
                "aircraft_registration": "G-INTE",
                "aircraft_assignment_role": "OPERATOR",
            },
            2,
        )
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as handle:
            handle.write(b"integration fixture")
            fixture_path = Path(handle.name)
        self.addCleanup(fixture_path.unlink, missing_ok=True)
        companies = CompanyStore(TEST_DATABASE_URL)
        arguments = {
            "source_code": "TEST_COMPANIES",
            "source_name": "Integration test companies",
            "source_kind": "CURATED",
            "authority_code": "UK_CAA",
            "source_url": "https://example.test/companies",
            "data_license": "Test fixture",
            "notes": None,
            "file_path": fixture_path,
        }
        first = companies.import_rows((parsed,), **arguments)
        second = companies.import_rows((parsed,), **arguments)
        self.assertEqual(first["companies"], 1)
        self.assertEqual(second["capabilities"], 1)

        with self.store.connect() as connection:
            company_row = connection.execute(
                """
                SELECT is_operator, is_mro, site_count, valid_approval_count,
                       capability_count, assigned_aircraft_count
                FROM company_directory
                WHERE company_key = 'integration-rotor-services-gb'
                """
            ).fetchone()
            self.assertEqual(company_row, (True, True, 1, 1, 1, 1))
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM company_import_batch WHERE source_code = 'TEST_COMPANIES'"
                ).fetchone()[0],
                2,
            )

        analytics = AnalyticsStore(self.store)
        directory = analytics.company_directory(
            company_query="Integration Rotor", company_role="mro"
        )
        approvals = analytics.company_approvals(
            approval_query="UK.145.INTEGRATION", approval_status="VALID"
        )
        capabilities = analytics.company_capabilities(
            capability_query="H-145", capability_kind="AIRCRAFT", approval_status="VALID"
        )
        operator_aircraft = analytics.operator_aircraft(
            company_query="Integration Rotor", assignment_role="OPERATOR"
        )
        fleet_summary = analytics.operator_fleet_summary()
        self.assertEqual(directory[0]["valid_approval_count"], 1)
        self.assertEqual(directory[0]["assigned_aircraft_count"], 1)
        self.assertEqual(approvals[0]["approval_number"], "UK.145.INTEGRATION")
        self.assertEqual(capabilities[0]["aircraft_type_code"], "H145")
        self.assertEqual(operator_aircraft[0]["registration"], "G-INTE")
        self.assertEqual(operator_aircraft[0]["assignment_role"], "OPERATOR")
        self.assertEqual(operator_aircraft[0]["geographic_region"], "EUROPE")
        self.assertEqual(operator_aircraft[0]["region_basis"], "COMPANY_COUNTRY")
        self.assertGreaterEqual(fleet_summary["operator_assignments"], 1)

    def test_zz_casa_registration_match_resolves_identity_and_operator(self) -> None:
        assert TEST_DATABASE_URL is not None
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE aircraft_day SET registration = 'VH-TST' WHERE address = 'abcdef'"
            )
            connection.execute(
                """
                INSERT INTO aircraft_type_classification (
                    type_code, category, classification_source, confidence
                ) VALUES ('H145', 'ROTORCRAFT', 'TEST_FIXTURE', 1.000)
                ON CONFLICT (type_code) DO UPDATE SET category = EXCLUDED.category
                """
            )
        record = CasaAircraftRecord(
            registration="VH-TST",
            address=None,
            manufacturer="TEXTRON AVIATION INC.",
            model="208B",
            serial_number="TEST-SERIAL-001",
            registry_category="Power Driven Aeroplane",
            official_category="FIXED_WING",
            registration_sub_type="Full Registration",
            registration_status="Full Registration",
            issue_date=None,
            effective_date=date(2026, 1, 1),
            ineffective_date=None,
            modified_date=None,
            manufacture_date=None,
            base_country="Australia",
            base_region=None,
            base_location=None,
            type_certificate_number="A37CE",
            engine_category="Turbo-prop",
            number_of_engines=1,
            number_of_seats=None,
            official_type_code="C208",
            registered_operator="INTEGRATION AIR SERVICES PTY LTD",
            operator_effective_date=date(2026, 1, 2),
            operator_country="Australia",
            operator_geographic_region="OCEANIA",
        )
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
            handle.write(b"CASA integration fixture")
            fixture_path = Path(handle.name)
        self.addCleanup(fixture_path.unlink, missing_ok=True)
        registry = AircraftRegistryStore(TEST_DATABASE_URL)
        registry.import_casa(
            (record,),
            snapshot_date=date(2026, 8, 24),
            downloaded_at=datetime.now(UTC),
            source_path=fixture_path,
        )
        with self.store.connect() as connection:
            identity = connection.execute(
                """
                SELECT type_code, resolved_category::text, identity_source_code,
                       registry_operator, source_type_code, identity_status
                FROM aircraft_day_identity
                WHERE address = 'abcdef'
                """
            ).fetchone()
        self.assertEqual(
            identity,
            (
                "C208", "FIXED_WING", "CASA_AIRCRAFT_REGISTER",
                "INTEGRATION AIR SERVICES PTY LTD", "H145", "CATEGORY_CONFLICT",
            ),
        )
        assignments = AnalyticsStore(self.store).operator_aircraft(
            registration_query="VH-TST"
        )
        self.assertEqual(assignments[0]["company"], "INTEGRATION AIR SERVICES PTY LTD")
        self.assertEqual(assignments[0]["source_code"], "CASA_AIRCRAFT_REGISTER")
        self.assertEqual(assignments[0]["geographic_region"], "OCEANIA")
        airport_tails = AnalyticsStore(self.store).airport_aircraft(
            date(2026, 8, 20),
            date(2026, 8, 20),
            airport_code="TEST",
            metric="airport_ground_observations",
        )
        self.assertEqual(airport_tails[0]["operator"], "INTEGRATION AIR SERVICES PTY LTD")
        self.assertEqual(
            airport_tails[0]["operator_source_code"], "CASA_AIRCRAFT_REGISTER"
        )
        with self.store.connect() as connection:
            semantic_identity = connection.execute(
                """
                SELECT registration, type_code, category, operator
                FROM nl_aircraft_activity
                WHERE utc_date = %s AND address = 'abcdef'
                """,
                (date(2026, 8, 20),),
            ).fetchone()
        self.assertEqual(
            semantic_identity,
            (
                "VH-TST", "C208", "FIXED_WING",
                "INTEGRATION AIR SERVICES PTY LTD",
            ),
        )

    def test_load_replace_and_failure_rollback(self) -> None:
        catalog, discovery, summary = self.fixture()
        self.store.upsert_airports(catalog)
        dataset_id = self.store.upsert_dataset(discovery)

        download_job = self.store.start_job(dataset_id, "DOWNLOAD")
        self.store.set_downloading(dataset_id)
        self.store.finish_download(
            dataset_id,
            download_job,
            raw_bytes=100,
            raw_file_count=1,
            downloaded_bytes_this_run=100,
            duration_ms=1,
        )

        process_job = self.store.start_job(dataset_id, "PROCESS")
        self.store.set_processing(dataset_id)
        result = self.store.load_summaries(dataset_id, process_job, iter([summary]))
        self.assertEqual(result["aircraft_count"], 1)
        self.assertEqual(result["airport_presence_count"], 1)
        self.assertEqual(result["flight_segment_count"], 1)
        self.assertEqual(result["airport_visit_count"], 1)
        with self.store.connect() as connection:
            initial_derived_bytes = connection.execute(
                "SELECT derived_bytes_estimate FROM dataset_day WHERE id = %s",
                (dataset_id,),
            ).fetchone()[0]
            episode_metadata = connection.execute(
                """
                SELECT flight_segment_record_count, airport_visit_record_count,
                       derivation_version, derivation_config
                FROM dataset_day
                WHERE id = %s
                """,
                (dataset_id,),
            ).fetchone()
        self.assertIsNotNone(initial_derived_bytes)
        self.assertEqual(episode_metadata[:3], (1, 1, DERIVATION_VERSION))
        self.assertEqual(episode_metadata[3], {})
        already_processed = self.store.enqueue_date(discovery.utc_date)
        self.assertEqual(already_processed["outcome"], "SKIPPED_ALREADY_PROCESSED")

        self.store.mark_raw_deleted(dataset_id, 100)
        redownload_job = self.store.start_job(dataset_id, "DOWNLOAD")
        self.store.set_downloading(dataset_id)
        self.store.finish_download(
            dataset_id,
            redownload_job,
            raw_bytes=100,
            raw_file_count=1,
            downloaded_bytes_this_run=100,
            duration_ms=2,
        )
        with self.store.connect() as connection:
            raw_state = connection.execute(
                "SELECT raw_deleted_at, raw_deleted_bytes FROM dataset_day WHERE id = %s",
                (dataset_id,),
            ).fetchone()
            self.assertEqual(raw_state, (None, None))

        reprocess_job = self.store.start_job(dataset_id, "REPROCESS")
        self.store.set_processing(dataset_id)
        self.store.load_summaries(dataset_id, reprocess_job, iter([summary]))

        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM aircraft_day").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT count(*) FROM aircraft_airport_day").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM aircraft_flight_segment").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM aircraft_airport_visit").fetchone()[0],
                1,
            )
            metrics = connection.execute(
                "SELECT unique_aircraft, type_code FROM airport_day_type_metrics"
            ).fetchone()
            self.assertEqual(metrics, (1, "H145"))
            hub_metrics = connection.execute(
                """
                SELECT unique_aircraft, primary_aircraft, known_type_codes
                FROM airport_day_metrics
                """
            ).fetchone()
            self.assertEqual(hub_metrics, (1, 1, 1))
            current_derived_bytes = connection.execute(
                "SELECT derived_bytes_estimate FROM dataset_day WHERE id = %s",
                (dataset_id,),
            ).fetchone()[0]
            self.assertEqual(current_derived_bytes, initial_derived_bytes)

        failed_job = self.store.start_job(dataset_id, "REPROCESS")
        self.store.set_processing(dataset_id)

        def failing_generator():
            yield summary
            raise RuntimeError("synthetic parser failure")

        with self.assertRaisesRegex(RuntimeError, "synthetic parser failure"):
            self.store.load_summaries(dataset_id, failed_job, failing_generator())
        self.store.fail_job(
            dataset_id,
            failed_job,
            stage="PROCESSING",
            message="synthetic parser failure",
        )
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM aircraft_day").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT observation_count FROM aircraft_day").fetchone()[0],
                summary[0].observation_count,
            )

    def test_sequential_queue_claim_duplicate_guard_and_cancel(self) -> None:
        queued = self.store.enqueue_date(date(2026, 8, 19))
        self.assertEqual(queued["outcome"], "QUEUED")
        duplicate = self.store.enqueue_date(date(2026, 8, 19))
        self.assertEqual(duplicate["outcome"], "ALREADY_QUEUED")

        item = self.store.claim_next("integration-test-worker")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.utc_date, date(2026, 8, 19))
        self.store.finish_queue_item(item, {"status": "fixture complete"})

        cancellable = self.store.enqueue_date(date(2026, 8, 18))
        cancelled = self.store.cancel_queue_item(cancellable["item"]["id"])
        self.assertIsNotNone(cancelled)
        with self.store.connect() as connection:
            statuses = connection.execute(
                "SELECT status, count(*) FROM ingestion_queue GROUP BY status ORDER BY status"
            ).fetchall()
        self.assertIn(("SUCCEEDED", 1), statuses)
        self.assertIn(("CANCELLED", 1), statuses)

    def test_worker_processes_queue_without_parallel_stage(self) -> None:
        seen_dates: list[date] = []

        def fixture_runner(args):
            seen_dates.append(args.utc_date)
            return {"status": "PROCESSED", "utc_date": args.utc_date.isoformat()}

        queued = self.store.enqueue_date(date(2026, 8, 17))
        self.assertEqual(queued["outcome"], "QUEUED")
        worker = SequentialIngestionWorker(
            self.store,
            runner=fixture_runner,
            poll_seconds=0.05,
        )
        worker.start()
        worker.notify()
        deadline = time.monotonic() + 5
        final_status = None
        while time.monotonic() < deadline:
            with self.store.connect() as connection:
                final_status = connection.execute(
                    "SELECT status FROM ingestion_queue WHERE id = %s",
                    (queued["item"]["id"],),
                ).fetchone()[0]
            if str(final_status) == "SUCCEEDED":
                break
            time.sleep(0.05)
        worker.stop()
        self.assertEqual(str(final_status), "SUCCEEDED")
        self.assertEqual(seen_dates, [date(2026, 8, 17)])

    def test_z_coverage_aware_analytics_snapshot(self) -> None:
        analytics = AnalyticsStore(self.store)
        analytics.refresh_after_ingestion()

        snapshot = analytics.snapshot(date(2026, 8, 20), date(2026, 8, 20))
        self.assertTrue(snapshot["coverage"]["complete"])
        self.assertEqual(snapshot["totals"]["unique_aircraft"], 1)
        self.assertEqual(snapshot["types"][0]["type_code"], "H145")
        self.assertEqual(snapshot["hubs"][0]["airport_ident"], "TEST")
        self.assertLessEqual(
            snapshot["hubs"][0]["movement_linked_aircraft"],
            snapshot["hubs"][0]["movement_candidates"],
        )
        self.assertEqual(snapshot["helicopters"]["totals"]["unique_aircraft"], 1)

        suggestions = analytics.operator_suggestions("integration rotor")
        self.assertEqual(suggestions[0]["name"], "Integration Rotor Services Ltd")

        partial = analytics.snapshot(date(2026, 8, 19), date(2026, 8, 20))
        self.assertFalse(partial["coverage"]["complete"])
        self.assertEqual(partial["coverage"]["available_days"], 1)
        self.assertEqual(partial["coverage"]["requested_days"], 2)

        site = analytics.company_sites(query="Integration Rotor")[0]
        updated = analytics.update_company_site_tracking(
            site["site_id"],
            airport_ident="TEST",
            is_of_interest=True,
            is_customer=True,
            interest_notes="Integration customer base",
        )
        self.assertTrue(updated["is_customer"])
        self.assertTrue(updated["is_of_interest"])
        self.assertEqual(updated["airport_ident"], "TEST")

        activity = analytics.company_site_activity(
            site["site_id"], date(2026, 8, 20)
        )
        self.assertEqual(activity["airport_metrics"]["unique_aircraft"], 1)
        self.assertEqual(activity["types"][0]["type_code"], "H145")
        self.assertIn("does not prove", activity["attribution_note"])

        with self.store.connect() as connection:
            semantic_row = connection.execute(
                """
                SELECT registration, type_code, category, airport
                FROM nl_airport_activity
                WHERE utc_date = %s AND address = 'abcdef'
                """,
                (date(2026, 8, 20),),
            ).fetchone()
        self.assertEqual(semantic_row, ("G-TEST", "H145", "ROTORCRAFT", "TEST"))
        with self.store.connect() as connection:
            region_row = connection.execute(
                """
                SELECT registration, type_code, activity_region, linked_airports
                FROM nl_region_activity
                WHERE utc_date = %s AND address = 'abcdef'
                """,
                (date(2026, 8, 20),),
            ).fetchone()
        self.assertEqual(region_row, ("G-TEST", "H145", "EUROPE", 1))

    def test_zy_intelligence_api_queries_match_materialized_views(self) -> None:
        service = IntelligenceService(self.store)
        coverage = service.coverage(date(2026, 8, 20), date(2026, 8, 20))
        self.assertTrue(coverage["complete"])

        activity = service.helicopter_activity(
            date(2026, 8, 20),
            date(2026, 8, 20),
            registrations=["G-TEST"],
            operator=None,
            airport="TEST",
            limit=10,
        )
        self.assertEqual(activity[0]["registration"], "G-TEST")
        self.assertEqual(activity[0]["type_code"], "H145")

        days = service.helicopter_days(
            "G-TEST", date(2026, 8, 20), date(2026, 8, 20)
        )
        self.assertEqual(days[0]["airports"][0]["ident"], "TEST")

        airport = service.airport_activity(
            "TEST", date(2026, 8, 20), date(2026, 8, 20), limit=10
        )
        self.assertEqual(airport["helicopters"][0]["registration"], "G-TEST")
        self.assertEqual(airport["types"][0]["type_code"], "H145")

        daily = service.aircraft_daily_activity(
            date(2026, 8, 20),
            date(2026, 8, 20),
            registrations=["G-TEST"],
            category="ALL",
        )
        self.assertEqual(daily[0]["registration"], "G-TEST")
        self.assertEqual(daily[0]["airports"][0]["ident"], "TEST")
        self.assertEqual(daily[0]["flight_segment_count"], 1)
        self.assertEqual(daily[0]["airport_visit_count"], 1)
        self.assertEqual(daily[0]["derivation_version"], DERIVATION_VERSION)
        self.assertGreaterEqual(daily[0]["elapsed_flight_hours"], 0)
        self.assertIsNotNone(daily[0]["data_revision"])

        ranked_aircraft = service.region_aircraft_rankings(
            ["EUROPE"],
            date(2026, 8, 20),
            date(2026, 8, 20),
            category="ROTORCRAFT",
            operator_status="ALL",
            type_status="KNOWN",
            metric="active_hours",
            limit=10,
        )
        self.assertEqual(ranked_aircraft[0]["registration"], "G-TEST")

        type_breakdown = service.region_type_breakdown(
            ["EUROPE"],
            date(2026, 8, 20),
            date(2026, 8, 20),
            category="ROTORCRAFT",
            limit=10,
        )
        self.assertEqual(type_breakdown[0]["type_code"], "H145")

        ranked_airports = service.region_airport_rankings(
            ["EUROPE"],
            date(2026, 8, 20),
            date(2026, 8, 20),
            category="ROTORCRAFT",
            metric="movement_candidates",
            limit=10,
            compare_previous=True,
        )
        self.assertEqual(ranked_airports[0]["airport_ident"], "TEST")
        self.assertEqual(ranked_airports[0]["leading_types"][0]["type_code"], "H145")

    def test_zz_external_access_users_and_audit_log(self) -> None:
        assert TEST_DATABASE_URL is not None
        access = AccessStore(TEST_DATABASE_URL)
        access.apply_migration()
        self.assertEqual(access.bootstrap_admins(["Admin@Example.com"]), 1)
        admin = access.get_user("admin@example.com")
        assert admin is not None
        self.assertTrue(admin.permits("ADMIN"))
        analyst = access.upsert_user(
            "analyst@example.com", role="ANALYST", display_name="Test Analyst"
        )
        self.assertEqual(analyst["role"], "ANALYST")
        access.record_audit_event(
            actor=AccessUser(
                email="admin@example.com",
                display_name=None,
                role="ADMIN",
                active=True,
            ),
            action="upsert_access_user",
            method="POST",
            path="/api/admin/users",
            request_id="integration-test",
            remote_address="127.0.0.1",
            response_status=200,
            target="email=analyst@example.com",
        )
        with self.store.connect() as connection:
            audit = connection.execute(
                """
                SELECT actor_email, action, remote_address::text
                FROM heligent_audit_event
                WHERE request_id = 'integration-test'
                """
            ).fetchone()
        self.assertEqual(
            audit, ("admin@example.com", "upsert_access_user", "127.0.0.1/32")
        )
        with self.assertRaisesRegex(ValueError, "own access"):
            access.upsert_user(
                "admin@example.com",
                role="VIEWER",
                actor_email="admin@example.com",
            )
        deactivated = access.deactivate_user(
            "analyst@example.com", actor_email="admin@example.com"
        )
        assert deactivated is not None
        self.assertFalse(deactivated["active"])
        access.upsert_user("spare-admin@example.com", role="ADMIN")
        deactivated_admin = access.deactivate_user(
            "admin@example.com", actor_email="spare-admin@example.com"
        )
        assert deactivated_admin is not None
        self.assertFalse(deactivated_admin["active"])
        with self.assertRaisesRegex(ValueError, "own account"):
            access.deactivate_user("admin@example.com", actor_email="admin@example.com")


if __name__ == "__main__":
    unittest.main()
