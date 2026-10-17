"""Maintenance boundary tests; optional integration uses a dedicated local test DB."""
from datetime import date
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from adsb_ingest.intelligence_api import create_intelligence_api
from adsb_ingest.maintenance_catalog import MaintenanceCatalog, validate_parameters
from adsb_ingest.postgres import PostgresStore
from adsb_ingest.webapp import create_app
from test_webapp import FakeAdminStore, FakeAnalyticsStore, FakeNaturalLanguage, FakeAccessStore


class FakeCatalog:
    def __init__(self):
        self.calls = []

    def companies(self, **kwargs):
        self.calls.append(('companies',kwargs))
        return {'data':[{'id':1,'name':'Example MRO'}], 'meta':{'dataset_revision':'one','next_cursor':None}}

    def capabilities(self, company_id, **kwargs):
        self.calls.append(('capabilities',company_id,kwargs))
        if company_id==999:
            raise LookupError('Active maintenance company not found')
        return {'data':{'company':{'id':company_id},'capabilities':[],'kind_counts':{}},
                'meta':{'dataset_revision':'one','next_cursor':None}}

    def coverage(self, **kwargs):
        self.calls.append(('coverage',kwargs))
        return {'data':{'totals':{},'countries':[],'sources':[],'unlocated':{},'definitions':{}},
                'meta':{'dataset_revision':'one'}}


class MaintenanceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.catalog = FakeCatalog()
        self.client = create_intelligence_api(token='test-only-secret',service=object(),maintenance_service=self.catalog).test_client()
        self.headers = {'Authorization':'Bearer test-only-secret'}

    def test_bearer_boundary_and_supported_filters(self):
        paths = ('/api/v1/companies', '/api/v1/companies/1/maintenance-capabilities', '/api/v1/maintenance-coverage')
        for path in paths:
            self.assertEqual(self.client.get(path).status_code,401)
            response = self.client.get(path,headers=self.headers)
            self.assertEqual(response.status_code,200)
            self.assertIn('data',response.json)
            self.assertEqual(response.json['meta']['dataset_revision'],'one')
            self.assertEqual(self.client.post(path,headers=self.headers).status_code,405)
        response = self.client.get('/api/v1/companies?site_country=GB&source=CAA&authority=UK_CAA&kind=AIRCRAFT&query=ACME',headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.catalog.calls[-1][1]['site_country'],'GB')

    def test_rejects_unsupported_historical_and_duplicate_parameters(self):
        for suffix in ('?as_of=2020-01-01','?country=GB&country=FR','?limit=20&sql=DROP'):
            response = self.client.get('/api/v1/companies'+suffix,headers=self.headers)
            self.assertEqual(response.status_code,400)
        self.assertEqual(self.client.get('/api/v1/companies/999/maintenance-capabilities',headers=self.headers).status_code,404)

    def test_ui_aliases_private_but_viewers_can_read(self):
        args = dict(start_worker=False,store=FakeAdminStore(),analytics_store=FakeAnalyticsStore(),natural_language=FakeNaturalLanguage())
        paths = ('/api/tools/maintenance/companies','/api/tools/maintenance/companies/1/capabilities','/api/tools/maintenance/coverage')
        demo = create_app(**args,public_demo=True).test_client()
        for path in paths:
            self.assertEqual(demo.get(path).status_code,404)
        with patch.dict('os.environ',{'HELIGENT_AUTH_PROXY_SECRET':'s'*40,'HELIGENT_BOOTSTRAP_ADMIN_EMAILS':''}):
            client = create_app(**args,access_store=FakeAccessStore(),auth_mode='NGROK').test_client()
            headers = {'X-Heligent-Proxy-Secret':'s'*40,'X-Heligent-Auth-Email':'viewer@example.com'}
            with patch('adsb_ingest.maintenance_catalog.MaintenanceCatalog',return_value=self.catalog):
                for path in paths:
                    self.assertIn(client.get(path).status_code,(401,403))
                    self.assertEqual(client.get(path,headers=headers).status_code,200)

    def test_bad_values_fail_before_opening_database(self):
        catalog = MaintenanceCatalog(None)
        for kwargs in ({'country':"GB' OR 1=1"},{'limit':501},{'cursor':'-1'},{'kind':'AOC'},{'role':'OPERATOR'},{'query':'x'*201},{'source':'a/b'}):
            with self.assertRaises(ValueError): catalog.companies(**kwargs)
        for kwargs in ({'geography':'authority_country'},{'authority':'a/b'},{'kind':'unknown'}):
            with self.assertRaises(ValueError): catalog.coverage(**kwargs)
        for value in (True,0,-1,'abc'):
            with self.assertRaises(ValueError): catalog.capabilities(value)


@unittest.skipUnless(os.getenv('HELIGENT_TEST_DATABASE_URL'), 'Set HELIGENT_TEST_DATABASE_URL to an isolated local PostgreSQL database')
class MaintenancePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        dsn = os.environ['HELIGENT_TEST_DATABASE_URL']
        info = conninfo_to_dict(dsn)
        if info.get('host') not in ('localhost','127.0.0.1','::1') or 'test' not in info.get('dbname','').lower():
            raise RuntimeError('Integration tests require a dedicated local database with test in its name')
        cls.schema = 'maintenance_test_'+uuid4().hex
        cls.base_dsn = dsn
        with psycopg.connect(dsn,autocommit=True) as c:
            c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(cls.schema)))
        cls.store = PostgresStore(make_conninfo(dsn,options='-c search_path='+cls.schema))
        with cls.store.connect() as c:
            c.execute('''CREATE TABLE company(id bigint PRIMARY KEY,name text,legal_name text,trading_name text,country_code text,is_mro boolean,active boolean);
                CREATE TABLE company_alias(id bigint PRIMARY KEY,company_id bigint,alias text);
                CREATE TABLE company_data_source(code text PRIMARY KEY,name text,data_license text,source_url text);
                CREATE TABLE company_import_batch(id bigint PRIMARY KEY,source_code text,status text,finished_at timestamptz,metadata jsonb);
                CREATE TABLE regulatory_approval(id bigint PRIMARY KEY,company_id bigint,authority_code text,approval_type text,approval_number text,status text,valid_from date,valid_to date,last_verified_at timestamptz,source_url text);
                CREATE TABLE company_site(id bigint PRIMARY KEY,company_id bigint,name text,country_code text,locality text,airport_ident text,latitude_deg float,longitude_deg float,address_line_1 text,active boolean);
                CREATE TABLE approval_capability(id bigint PRIMARY KEY,regulatory_approval_id bigint,company_site_id bigint,source_code text,capability_kind text,rating_class text,rating_code text,manufacturer text,model text,aircraft_type_code text,limitation text,is_base_maintenance boolean,is_line_maintenance boolean,active boolean);
                CREATE TABLE capability_aircraft_mapping(id bigint PRIMARY KEY,capability_id bigint,aircraft_type_code text,match_level text,variant_scope text,source_url text,notes text,evidence_snapshot jsonb,active boolean,revision int,reviewed_by text,reviewed_at timestamptz);
                CREATE TABLE capability_phrase_mapping(id bigint PRIMARY KEY,model_phrase text,normalized_phrase text,aircraft_type_code text,match_level text,variant_scope text,source_url text,notes text,active boolean,revision int,reviewed_by text,reviewed_at timestamptz);
                CREATE TABLE company_site_external_identifier(source_code text,company_site_id bigint,active boolean);
                CREATE TABLE company_external_identifier(source_code text,company_id bigint,active boolean);
                CREATE TABLE regulatory_approval_external_identifier(source_code text,regulatory_approval_id bigint,active boolean);
                CREATE FUNCTION heligent_world_region(text) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT CASE WHEN $1 IN ('GB','FR','DE') THEN 'EUROPE' WHEN $1='CN' THEN 'ASIA' END $$;
                CREATE TABLE aircraft_registry_source(code text,name text);
                CREATE TABLE aircraft_registry_import_batch(id bigint,source_code text,status text,snapshot_date date,downloaded_at timestamptz,created_at timestamptz,source_file_name text,source_sha256 text,row_count int,metadata jsonb);
                ALTER TABLE company_import_batch ADD COLUMN started_at timestamptz DEFAULT now(),ADD COLUMN file_name text,ADD COLUMN sha256 text,ADD COLUMN imported_rows int,ADD COLUMN company_count int,ADD COLUMN site_count int,ADD COLUMN capability_count int,ADD COLUMN error_message text;
                CREATE TABLE tar1090_attempt(id bigint,status text,snapshot_id bigint,finished_at timestamptz,started_at timestamptz,error_message text);
                CREATE TABLE tar1090_snapshot(id bigint,source_date date,metadata jsonb);
                CREATE TABLE tar1090_preview(id bigint,created_at timestamptz,report jsonb);
                CREATE TABLE tool_source_lookup(id bigint,source_code text,address text,status text,fetched_at timestamptz,source_url text,result jsonb);
                CREATE TABLE tool_source_settings(source_code text);
                CREATE TABLE tool_import_preview(adapter text,created_at timestamptz,snapshot jsonb);
                CREATE TABLE reference_dataset(code text,source_url text,downloaded_at timestamptz,row_count int);
                CREATE TABLE dataset_day(utc_date date,status text);
                INSERT INTO company VALUES(1,'Alpha MRO','Alpha Ltd',NULL,'GB',true,true),(2,'China MRO',NULL,NULL,'CN',true,true),(3,'Inactive',NULL,NULL,'FR',true,false);
                INSERT INTO company_alias VALUES(1,1,'Former Alpha');
                INSERT INTO company_data_source VALUES('CAA','CAA',NULL,NULL),('EASA','EASA',NULL,NULL);
                INSERT INTO company_import_batch(id,source_code,status,finished_at,metadata) VALUES(1,'CAA','SUCCEEDED','2026-09-01','{}'),(2,'EASA','SUCCEEDED','2026-09-02','{"fetched_at":"2026-08-31T12:00:00Z"}');
                INSERT INTO regulatory_approval VALUES(10,1,'UK_CAA','PART_145','UK.145.1','VALID',NULL,NULL,'2026-08-30','https://example.com/caa'),
                    (11,1,'UK_CAA','PART_145','UK.145.2','SUSPENDED',NULL,NULL,NULL,NULL),
                    (12,1,'UK_CAA','AOC','UK.AOC.1','VALID',NULL,NULL,NULL,NULL),
                    (20,2,'EASA','PART_145','EASA.145.1','VALID',NULL,NULL,NULL,NULL);
                INSERT INTO company_site VALUES(100,1,'London','GB','London','EGLL',51.4,-0.4,NULL,true),
                    (101,1,'Unlocated',NULL,'Unknown',NULL,NULL,NULL,NULL,true),(102,1,'Closed','FR','Paris',NULL,NULL,NULL,NULL,false);
                INSERT INTO approval_capability VALUES(1000,10,100,'CAA','AIRCRAFT','A3','A3',NULL,'EC135',NULL,'Line only',false,true,true),
                    (1001,10,101,'CAA','AIRCRAFT','A3','A3',NULL,'BK117',NULL,'Restricted variant',false,true,true),
                    (1002,10,NULL,'CAA','ENGINE','B1','B1',NULL,'Engine',NULL,'Engine scope',false,false,true),
                    (1003,11,100,'CAA','AIRCRAFT','A3','A3',NULL,'EC145','EC45','Suspended scope',true,true,true),
                    (1004,12,100,'CAA','AIRCRAFT','A3','A3',NULL,'AOC aircraft','S76','AOC scope',false,false,true),
                    (1005,10,102,'CAA','AIRCRAFT','A3','A3',NULL,'Closed','S76','Inactive site',false,false,true),
                    (2000,20,NULL,'EASA','AIRCRAFT','A1','A1',NULL,NULL,'B738','Boeing scope',true,false,true);
                INSERT INTO capability_phrase_mapping VALUES(1,'EC135','ec135','EC35','REVIEWED_TYPE','','https://example.com','Reviewed',true,1,'reviewer',now()),
                    (2,'BK117','bk117','EC45','POSSIBLE_FAMILY','D-2','https://example.com','Family',true,1,'reviewer',now());''')
        cls.catalog = MaintenanceCatalog(cls.store)

    @classmethod
    def tearDownClass(cls):
        import psycopg
        from psycopg import sql
        with psycopg.connect(cls.base_dsn,autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(cls.schema)))

    def test_capabilities_paginate_preserve_scope_and_exclude_aoc_and_inactive(self):
        first = self.catalog.capabilities(1,kind='ALL',limit=2)
        self.assertEqual([r['capability_id'] for r in first['data']['capabilities']],[1000,1001])
        second = self.catalog.capabilities(1,kind='ALL',limit=2,cursor=first['meta']['next_cursor'])
        self.assertEqual([r['capability_id'] for r in second['data']['capabilities']],[1002,1003])
        self.assertIsNone(second['meta']['next_cursor'])
        self.assertEqual(first['meta']['dataset_revision'],second['meta']['dataset_revision'])
        self.assertEqual(first['data']['capabilities'][0]['type_interpretation'],'REVIEWED_TYPE')
        self.assertEqual(first['data']['capabilities'][1]['type_interpretation'],'POSSIBLE_FAMILY')
        self.assertIsNone(second['data']['capabilities'][0]['site'])
        self.assertFalse(second['data']['capabilities'][1]['approval_current'])
        with self.assertRaises(LookupError): self.catalog.capabilities(3)

    def test_coverage_distinguishes_domicile_site_geography_and_scope_rows(self):
        result = self.catalog.coverage()
        totals = result['data']['totals']
        self.assertEqual(totals['capabilities'],5)
        self.assertEqual(totals['aircraft_capabilities'],4)
        self.assertEqual(totals['distinct_type_codes'],3)
        self.assertEqual(totals['current_approvals'],2)
        self.assertEqual(result['data']['unlocated']['capabilities'],3)
        self.assertEqual(result['data']['unlocated']['sites'],1)
        self.assertEqual([r['country_code'] for r in result['data']['countries']],['GB'])
        china = self.catalog.coverage(geography='company_country',source='EASA')['data']
        self.assertEqual(china['countries'][0]['country_code'],'CN')
        self.assertEqual(china['totals']['sites'],0)
        self.assertEqual(self.catalog.coverage(source='NONE')['data']['totals']['capabilities'],0)

    def test_company_search_filters_aliases_and_physical_country_without_domicile_fallback(self):
        self.assertEqual(self.catalog.companies(query='Former Alpha')['data'][0]['id'],1)
        self.assertEqual(self.catalog.companies(query='EASA.145.1')['data'][0]['id'],2)
        self.assertEqual(self.catalog.companies(country='CN')['data'][0]['id'],2)
        self.assertEqual(self.catalog.companies(site_country='CN')['data'],[])
        self.assertEqual(self.catalog.companies(site_country='GB',source='EASA')['data'],[])
        first = self.catalog.companies(limit=1)
        self.assertEqual(first['meta']['next_cursor'],'1')
        self.assertEqual(self.catalog.companies(cursor='1')['data'][0]['id'],2)

    def test_withdrawn_local_override_blocks_shared_and_revision_detects_change(self):
        from psycopg.types.json import Jsonb
        from adsb_ingest.capability_mappings import evidence_snapshot
        before = self.catalog.capabilities(1)['meta']['dataset_revision']
        row = self.catalog.capabilities(1)['data']['capabilities'][0]
        with self.store.connect() as c:
            c.execute('''INSERT INTO capability_aircraft_mapping VALUES(1,1000,'EC35','REVIEWED_TYPE','','https://example.com','Withdrawn',%s,false,1,'reviewer',now())''',
                (Jsonb(evidence_snapshot(row)),))
        try:
            result = self.catalog.capabilities(1)
            cap = result['data']['capabilities'][0]
            self.assertEqual(cap['type_interpretation'],'UNMAPPED')
            self.assertEqual(cap['mappings'][0]['origin'],'LOCAL')
            self.assertNotIn('reviewed_by',cap['mappings'][0])
            self.assertNotIn('notes',cap['mappings'][0])
            self.assertNotIn('evidence_snapshot',cap['mappings'][0])
            self.assertNotEqual(before,result['meta']['dataset_revision'])
            self.assertEqual(self.catalog.coverage()['data']['totals']['distinct_type_codes'],2)
            with self.store.connect() as c:
                c.execute('UPDATE capability_aircraft_mapping SET active=true WHERE id=1')
                c.execute("UPDATE approval_capability SET limitation='Changed scope' WHERE id=1000")
            cap = self.catalog.capabilities(1)['data']['capabilities'][0]
            self.assertTrue(cap['mappings'][0]['stale'])
            self.assertEqual(cap['type_interpretation'],'UNMAPPED')
            self.assertEqual(self.catalog.coverage()['data']['totals']['distinct_type_codes'],2)
        finally:
            with self.store.connect() as c:
                c.execute('DELETE FROM capability_aircraft_mapping WHERE id=1')
                c.execute("UPDATE approval_capability SET limitation='Line only' WHERE id=1000")

    def test_read_transaction_enforced_and_expiry_evaluated_today(self):
        import psycopg
        with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
            with self.catalog._read() as c:
                c.execute("UPDATE company SET name='Forbidden' WHERE id=1")
        with self.store.connect() as c:
            c.execute("UPDATE regulatory_approval SET valid_to=CURRENT_DATE-1 WHERE id=10")
        try:
            self.assertFalse(self.catalog.capabilities(1)['data']['capabilities'][0]['approval_current'])
        finally:
            with self.store.connect() as c:
                c.execute('UPDATE regulatory_approval SET valid_to=NULL WHERE id=10')

    def test_inventory_company_only_and_site_links_retain_distinct_geography_and_dates(self):
        from adsb_ingest.source_tools import source_inventory
        inventory = {r['code']:r for r in source_inventory(self.store)['sources']}
        self.assertEqual(inventory['CAA']['imported_site_countries'],['GB'])
        self.assertEqual(inventory['EASA']['imported_site_countries'],[])
        self.assertEqual(inventory['EASA']['imported_company_countries'],['CN'])
        self.assertEqual(inventory['EASA']['imported_countries'],['CN'])
        self.assertEqual(inventory['EASA']['last_fetched_at'],'2026-08-31T12:00:00Z')
        self.assertIsNone(inventory['EASA']['source_published_at'])
        self.assertEqual(inventory['EASA']['freshness']['status'],'UNKNOWN')
        self.assertIsNotNone(inventory['CAA']['last_verified_at'])


if __name__ == '__main__':
    unittest.main()
