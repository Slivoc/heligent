"""Read existing ingestion evidence without confusing import time with source age."""
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

from psycopg.rows import dict_row

from .source_catalog import CATALOG, REGIONS, source


def freshness(snapshot_date, refresh_days, *, has_evidence=False, today=None):
    today = today or datetime.now(UTC).date()
    if snapshot_date is None:
        return {'status': 'UNKNOWN' if has_evidence else 'NEVER', 'age_days': None}
    if isinstance(snapshot_date, datetime):
        snapshot_date = snapshot_date.date()
    age = (today - snapshot_date).days
    return {'status': 'FUTURE' if age < 0 else 'STALE' if age > refresh_days else 'CURRENT',
            'age_days': age}


def source_inventory(store):
    catalog = {s['code']: deepcopy(s) for s in CATALOG}
    with store.connect() as c:
        c.row_factory = dict_row
        c.execute("SET LOCAL statement_timeout='10s'")
        for row in c.execute('SELECT * FROM aircraft_registry_source ORDER BY name').fetchall():
            entry = catalog.setdefault(row['code'], source(row['code'], row['name'],
                countries=(row['country_code'],), scope='Registered national aircraft source.'))
            entry.update(license=row['data_license'], attribution=row['attribution'], enabled=row['active'])
        companies = c.execute('''SELECT s.*, ARRAY(SELECT DISTINCT cs.country_code
            FROM company_site_external_identifier e JOIN company_site cs ON cs.id=e.company_site_id
            WHERE e.source_code=s.code AND e.active AND cs.country_code IS NOT NULL
            ORDER BY cs.country_code) AS imported_countries,
            ARRAY(SELECT DISTINCT heligent_world_region(cs.country_code)
            FROM company_site_external_identifier e JOIN company_site cs ON cs.id=e.company_site_id
            WHERE e.source_code=s.code AND e.active AND heligent_world_region(cs.country_code) IS NOT NULL) AS imported_regions
            FROM company_data_source s ORDER BY s.name''').fetchall()
        for row in companies:
            if row['code'] in catalog and catalog[row['code']]['group'] != 'COMPANY':
                continue
            entry = catalog.setdefault(row['code'], source(row['code'], row['name'],
                group='COMPANY', method='CSV import / mapping', regions=(),
                fields=('Companies', 'Sites', 'Approvals', 'Capabilities', 'Assignments'),
                match='Source identifiers', url=row['source_url'] or '',
                scope='Imported company source; geographic scope has not been declared.',
                limitations='Countries below describe imported sites only. They do not establish national coverage. Upstream snapshot dates are not recorded by the generic CSV importer.',
                next_step='Validate the source-specific mapping, then use the company CSV importer.',
                command='heligent-companies validate-csv --file <mapped.csv>'))
            entry.update(license=row['data_license'], imported_countries=row['imported_countries'])
            if not entry['regions']:
                region_codes = {'EUROPE':'EU', 'NORTH_AMERICA':'NA', 'SOUTH_AMERICA':'SA',
                                'AFRICA':'AF', 'ASIA':'AS', 'OCEANIA':'OC', 'ANTARCTICA':'AN'}
                entry['regions'] = [region_codes[r] for r in row['imported_regions'] if r in region_codes]
        batches = c.execute('''SELECT * FROM (
            SELECT source_code,id,status,snapshot_date,downloaded_at AS fetched_at,
                   created_at AS imported_at,source_file_name AS file_name,source_sha256 AS sha256,
                   row_count AS rows,metadata,created_at AS attempted_at, row_number() OVER
                   (PARTITION BY source_code ORDER BY snapshot_date DESC,id DESC) AS rank,
                   row_number() OVER (PARTITION BY source_code,status ORDER BY snapshot_date DESC,id DESC) AS status_rank,
                   row_number() OVER (PARTITION BY source_code ORDER BY id DESC) AS attempt_rank
            FROM aircraft_registry_import_batch) b WHERE rank<=25 OR status_rank=1 OR attempt_rank=1
            ORDER BY source_code,rank''').fetchall()
        company_batches = c.execute('''SELECT * FROM (
            SELECT source_code,id,status,NULL::date AS snapshot_date, NULL::timestamptz AS fetched_at,
                   finished_at AS imported_at,started_at,file_name,sha256,imported_rows AS rows,
                   company_count,site_count,capability_count,error_message,metadata,started_at AS attempted_at,
                   row_number() OVER (PARTITION BY source_code ORDER BY id DESC) AS rank,
                   row_number() OVER (PARTITION BY source_code,status ORDER BY id DESC) AS status_rank
            FROM company_import_batch) b WHERE rank<=25 OR status_rank=1 ORDER BY source_code,rank''').fetchall()
        tar_batches = c.execute('''SELECT * FROM (SELECT 'TAR1090_DB' AS source_code,
            a.id,a.status,s.source_date::date AS snapshot_date,a.finished_at AS imported_at,
            a.started_at AS fetched_at,a.started_at AS attempted_at,'aircraft.csv.gz' AS file_name,
            s.metadata->>'sha256' AS sha256,(s.metadata->'field_coverage'->>'rows')::integer AS rows,
            s.metadata,a.error_message,row_number() OVER (ORDER BY a.id DESC) AS rank,
            row_number() OVER (PARTITION BY a.status ORDER BY a.id DESC) AS status_rank
            FROM tar1090_attempt a LEFT JOIN tar1090_snapshot s ON s.id=a.snapshot_id) b
            WHERE rank<=25 OR status_rank=1 ORDER BY rank''').fetchall()
        tar_preview = c.execute('''SELECT id,created_at,report->'counts' AS counts,report->>'region' AS region,
            report->>'from' AS "from",report->>'to' AS "to" FROM tar1090_preview ORDER BY created_at DESC LIMIT 1''').fetchone()
        lookups = c.execute('''SELECT * FROM (SELECT source_code,id,address,status,fetched_at,
            source_url,result,row_number() OVER (PARTITION BY source_code ORDER BY id DESC) AS rank
            FROM tool_source_lookup) l WHERE rank<=25''').fetchall()
        settings = {s['source_code']: s for s in c.execute('SELECT * FROM tool_source_settings').fetchall()}
        previews = {r['adapter']: r for r in c.execute('''SELECT DISTINCT ON (adapter)
            adapter,created_at,snapshot->>'query' AS query,snapshot->>'fetched_at' AS fetched_at,
            snapshot->>'truncated' AS truncated FROM tool_import_preview ORDER BY adapter,created_at DESC''').fetchall()}
        references = c.execute('SELECT * FROM reference_dataset ORDER BY code').fetchall()
        activity = c.execute('''SELECT max(utc_date) FILTER (WHERE status='PROCESSED') AS latest,
            count(*) FILTER (WHERE status='PROCESSED') AS processed_days,
            count(*) FILTER (WHERE status::text LIKE 'FAILED%') AS failed_days FROM dataset_day''').fetchone()
    for row in references:
        catalog[row['code']] = source(row['code'], row['code'].replace('_', ' ').title(),
            group='REFERENCE', method='Reference download', fields=('Airport reference',),
            match='Airport identifier', url=row['source_url'],
            scope='Imported reference dataset; count is reference rows, not aircraft.',
            limitations='Download time is known; upstream revision time is not recorded.',
            next_step='Reference refresh is part of the ingestion workflow.')
        catalog[row['code']].update(reference=row, last_imported_at=row['downloaded_at'], last_fetched_at=row['downloaded_at'],
                                   record_count=row['row_count'])
    for entry in catalog.values():
        code = entry['code']
        entry['settings'] = settings.get(code)
        entry['refresh_days'] = settings.get(code, {}).get('refresh_days', entry['refresh_days'])
        evidence = [b for b in batches + company_batches + tar_batches if b['source_code'] == code]
        entry['history'] = [b for b in evidence if b['rank'] <= 25]
        entry['lookups'] = [l for l in lookups if l['source_code'] == code]
        success = next((b for b in evidence if b['status'] in ('IMPORTED', 'SUCCEEDED', 'UNCHANGED')), None)
        entry['last_successful_import'] = success
        entry['latest_preview'] = previews.get(code)
        if success:
            entry.update(snapshot_date=success['snapshot_date'], last_imported_at=success['imported_at'],
                         last_fetched_at=success['fetched_at'], record_count=success['rows'])
        if entry['lookups']:
            entry['last_fetched_at'] = entry['lookups'][0]['fetched_at']
        if entry['latest_preview']:
            entry['last_fetched_at'] = entry['latest_preview']['fetched_at'] or entry['latest_preview']['created_at']
        entry['freshness'] = freshness(entry.get('snapshot_date'), entry['refresh_days'],
            has_evidence=bool(success or entry['lookups'] or entry.get('reference') or entry['latest_preview']))
        entry['has_import'] = bool(success or entry.get('reference'))
        attempt = max(evidence, key=lambda b: b['attempted_at'], default=None)
        entry['latest_failed'] = bool(attempt and attempt['status'] == 'FAILED'
                                     or entry['lookups'] and entry['lookups'][0]['status'] == 'FAILED')
        entry['schedule'] = ('On demand; no automatic lookup or scraping' if entry['readiness'] == 'LOOKUP'
            else 'Not connected' if entry['readiness'] == 'PLANNED'
            else 'No schedule recorded here; external server timers are not monitored')
        if code == 'TAR1090_DB':
            entry.update(schedule='On demand from this workspace; no background refresh', bulk_preview=tar_preview,
                         attribution='tar1090-db / wiedehopf; Mictronics aircraft database; ADSB Exchange')
            if success:
                entry['field_coverage'] = success['metadata']['field_coverage']
                entry['bulk_metadata'] = success['metadata']
    trace = catalog['ADSB_LOL']
    trace.update(snapshot_date=activity['latest'], activity=activity, has_import=bool(activity['processed_days']))
    trace['freshness'] = freshness(activity['latest'], trace['refresh_days'])
    return {'generated_at': datetime.now(UTC), 'sources': list(catalog.values()),
            'regions': REGIONS,
            'history_note': 'Up to 25 stored batches or lookup attempts per source. Older importers may roll back failed attempts, so absence of failures does not prove successful runs.'}


def source_detail(store, code):
    entry = next((s for s in source_inventory(store)['sources'] if s['code'] == code), None)
    if entry is None:
        raise ValueError('Unknown source')
    if entry['code'] in ('TC_CCAR', 'FAA_AIRCRAFT_REGISTRY', 'CASA_AIRCRAFT_REGISTER'):
        batch = entry['last_successful_import']
        if batch:
            with store.connect() as c:
                c.row_factory = dict_row
                c.execute("SET LOCAL statement_timeout='10s'")
                entry['field_coverage'] = c.execute('''SELECT count(*) AS rows,
                    count(nullif(btrim(address),'')) AS hex,
                    count(nullif(btrim(registration),'')) AS tail,
                    count(nullif(btrim(official_type_code),'')) AS icao_type,
                    count(*) FILTER (WHERE official_category<>'UNKNOWN') AS category,
                    count(*) FILTER (WHERE official_category='ROTORCRAFT') AS rotorcraft
                    FROM aircraft_registry_record WHERE import_batch_id=%s''', (batch['id'],)).fetchone()
    return entry


def save_source_settings(store, code, payload, actor):
    if not isinstance(payload, dict):
        raise ValueError('Expected source settings')
    days, notes = payload.get('refresh_days'), payload.get('notes', '')
    if type(days) is not int or not 1 <= days <= 3650 or not isinstance(notes, str) or len(notes) > 4000:
        raise ValueError('Use a review interval of 1–3650 days and notes of at most 4000 characters')
    if code not in {s['code'] for s in source_inventory(store)['sources']}:
        raise ValueError('Unknown source')
    with store.connect() as c:
        c.execute('''INSERT INTO tool_source_settings(source_code,refresh_days,notes,updated_by)
            VALUES (%s,%s,%s,%s) ON CONFLICT(source_code) DO UPDATE SET
            refresh_days=excluded.refresh_days,notes=excluded.notes,
            updated_by=excluded.updated_by,updated_at=now()''', (code, days, notes.strip(), actor))
    return {'saved': True}


def identity_gaps(store, start=None, end=None):
    # Aggregate each address once per region. Airport visits are geography evidence,
    # never registration jurisdiction; unlocated aircraft are retained explicitly.
    with store.connect() as c:
        c.row_factory = dict_row
        c.execute("SET LOCAL statement_timeout='20s'")
        latest = c.execute("SELECT max(utc_date) AS day FROM dataset_day WHERE status='PROCESSED'").fetchone()['day']
        end = date.fromisoformat(end) if end else latest or date.today()
        start = date.fromisoformat(start) if start else end - timedelta(days=6)
        if end < start or (end-start).days > 30:
            raise ValueError('Choose an ordered range of at most 31 days')
        coverage = c.execute("SELECT count(*) AS days FROM dataset_day WHERE status='PROCESSED' AND utc_date BETWEEN %s AND %s", (start, end)).fetchone()['days']
        rows = c.execute('''WITH eligible AS MATERIALIZED (
            SELECT d.* FROM aircraft_day_identity d JOIN dataset_day q ON q.id=d.dataset_day_id
            WHERE q.status='PROCESSED' AND d.utc_date BETWEEN %s AND %s AND d.position_count>0
        ), locations AS (
            SELECT DISTINCT v.dataset_day_id,v.address,ap.continent AS region
            FROM aircraft_airport_visit v JOIN airport ap ON ap.ident=v.airport_ident
            JOIN dataset_day q ON q.id=v.dataset_day_id
            WHERE q.utc_date BETWEEN %s AND %s AND q.status='PROCESSED' AND ap.continent IS NOT NULL
        ), scoped AS (
            SELECT d.*,coalesce(l.region,'UNLOCATED') AS region FROM eligible d
            LEFT JOIN locations l USING(dataset_day_id,address)
        ) SELECT CASE WHEN grouping(region)=1 THEN 'GLOBAL' ELSE region END AS region,
            count(DISTINCT address) AS aircraft,
            count(DISTINCT address) FILTER (WHERE nullif(btrim(registration),'') IS NULL) AS missing_tail,
            count(DISTINCT address) FILTER (WHERE nullif(btrim(type_code),'') IS NULL) AS missing_type,
            count(DISTINCT address) FILTER (WHERE nullif(btrim(registration),'') IS NULL
                AND nullif(btrim(type_code),'') IS NULL) AS missing_both,
            count(DISTINCT address) FILTER (WHERE resolved_category='UNKNOWN') AS unclassified,
            count(DISTINCT address) FILTER (WHERE resolved_category='ROTORCRAFT') AS rotorcraft,
            count(DISTINCT address) FILTER (WHERE resolved_category='ROTORCRAFT'
                AND nullif(btrim(registration),'') IS NULL) AS rotorcraft_missing_tail,
            count(DISTINCT address) FILTER (WHERE nullif(btrim(source_registration),'') IS NULL
                AND nullif(btrim(registration),'') IS NOT NULL) AS recovered_tail
            FROM scoped GROUP BY GROUPING SETS ((region),())''', (start, end, start, end)).fetchall()
    return {'from': start, 'to': end, 'processed_days': coverage,
            'expected_days': (end-start).days+1, 'rows': rows}
