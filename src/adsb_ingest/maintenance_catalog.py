"""Read-only, provenance-preserving maintenance catalogue shared by both APIs.

These are recorded regulatory scopes, not findings that a check can be performed.
No acquisition, geocoding, interpretation or catalogue mutation happens on reads.
"""
from contextlib import contextmanager
from datetime import UTC, datetime
import re

from psycopg.rows import dict_row

from .capability_mappings import effective_mappings, model_phrase, normalize_phrase


KINDS = ('AIRCRAFT', 'ENGINE', 'COMPONENT', 'SPECIALIST', 'SERVICE', 'OTHER')
MAINTENANCE_TYPES = ('PART145', 'EASAPART145', 'UKPART145', 'EASA145', 'UK145')
MAINTENANCE_SQL = "regexp_replace(upper(r.approval_type),'[^A-Z0-9]','','g')=ANY(%s)"
CURRENT_SQL = "(r.status='VALID' AND (r.valid_from IS NULL OR r.valid_from<=CURRENT_DATE) AND (r.valid_to IS NULL OR r.valid_to>=CURRENT_DATE))"


def _choice(value, choices, default, field):
    value = default if value in (None, '') else str(value).strip().upper()
    if value not in choices:
        raise ValueError(f"{field} must be one of: {', '.join(choices)}")
    return value


def _positive(value, default, maximum, field, *, allow_zero=False):
    value = default if value in (None, '') else value
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]{1,19}', str(value)):
        raise ValueError(f'{field} must be an integer')
    value = int(value)
    if not (0 if allow_zero else 1) <= value <= maximum:
        raise ValueError(f'{field} is outside the allowed range')
    return value


def _code(value, field, *, country=False):
    if value in (None, ''):
        return None
    value = str(value).strip().upper()
    if not re.fullmatch(r'[A-Z]{2}' if country else r'[A-Z0-9][A-Z0-9_.-]{0,79}', value):
        raise ValueError(f'Invalid {field}')
    return value


def validate_parameters(parameters, allowed):
    """Reject unsupported historical filters rather than imply historical truth."""
    unknown = set(parameters) - set(allowed)
    if unknown:
        raise ValueError('Unsupported parameters: ' + ', '.join(sorted(unknown)))
    if hasattr(parameters, 'getlist') and any(len(parameters.getlist(key)) != 1 for key in parameters):
        raise ValueError('Query parameters may only be supplied once')


class MaintenanceCatalog:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def _read(self):
        with self.store.connect() as c:
            c.row_factory = dict_row
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            c.execute("SET LOCAL statement_timeout='15s'")
            yield c

    @staticmethod
    def _meta(c, **extra):
        # A content fingerprint also observes withdrawals and edits to older rows.
        # All pages can be checked against the same revision by synchronizing clients.
        tables = ('company', 'company_alias', 'company_site', 'regulatory_approval', 'approval_capability',
                  'capability_aircraft_mapping', 'capability_phrase_mapping', 'company_data_source',
                  'company_import_batch')
        parts = []
        for table in tables:
            # Identifiers are fixed above; never derived from user input.
            order = 'code' if table == 'company_data_source' else 'id'
            row = c.execute(f"SELECT md5(coalesce(string_agg(md5(row_to_json(t)::text),'' ORDER BY {order}),'')) AS revision FROM {table} t").fetchone()
            parts.append(row['revision'])
        from hashlib import sha256
        return {'generated_at': datetime.now(UTC),
                'dataset_revision': sha256(''.join(parts).encode()).hexdigest(), **extra}

    def companies(self, *, query='', role='MRO', country=None, site_country=None,
                  source=None, authority=None, kind='ALL', cursor=None, limit=None):
        query = ' '.join(str(query or '').split())
        if len(query) > 200 or any(ord(ch) < 32 for ch in query):
            raise ValueError('query is capped at 200 printable characters')
        role = _choice(role, ('MRO',), 'MRO', 'role')
        country = _code(country, 'country', country=True)
        site_country = _code(site_country, 'site_country', country=True)
        source = _code(source, 'source')
        authority = _code(authority, 'authority')
        kind = _choice(kind, (*KINDS, 'ALL'), 'ALL', 'kind')
        after = _positive(cursor, 0, 9223372036854775807, 'cursor', allow_zero=True)
        limit = _positive(limit, 50, 500, 'limit')
        pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        with self._read() as c:
            rows = c.execute(f'''SELECT co.id,co.name,co.legal_name,co.trading_name,co.country_code,
                co.is_mro,count(r.id) AS approval_count
                FROM company co JOIN regulatory_approval r ON r.company_id=co.id AND {MAINTENANCE_SQL}
                WHERE co.active AND co.id>%s AND (%s::text IS NULL OR co.country_code=%s)
                  AND (%s::text IS NULL OR r.authority_code=%s)
                  AND EXISTS(SELECT 1 FROM approval_capability ac LEFT JOIN company_site s ON s.id=ac.company_site_id
                    WHERE ac.regulatory_approval_id=r.id AND ac.active AND (s.id IS NULL OR s.active)
                      AND (%s::text IS NULL OR s.country_code=%s) AND (%s::text IS NULL OR ac.source_code=%s)
                      AND (%s='ALL' OR ac.capability_kind=%s))
                  AND (concat_ws(' ',co.name,co.legal_name,co.trading_name) ILIKE %s
                       OR r.approval_number ILIKE %s
                       OR EXISTS(SELECT 1 FROM company_alias a WHERE a.company_id=co.id AND a.alias ILIKE %s))
                GROUP BY co.id ORDER BY co.id LIMIT %s''',
                (list(MAINTENANCE_TYPES),after,country,country,authority,authority,site_country,site_country,
                 source,source,kind,kind,pattern,pattern,pattern,limit+1)).fetchall()
            return {'data': rows[:limit], 'meta': self._meta(c,
                next_cursor=str(rows[limit-1]['id']) if len(rows)>limit else None)}

    def capabilities(self, company_id, *, kind='AIRCRAFT', cursor=None, limit=None):
        company_id = _positive(company_id, None, 9223372036854775807, 'company_id')
        kind = _choice(kind, (*KINDS, 'ALL'), 'AIRCRAFT', 'kind')
        after = _positive(cursor, 0, 9223372036854775807, 'cursor', allow_zero=True)
        limit = _positive(limit, 100, 500, 'limit')
        with self._read() as c:
            company = c.execute(f'''SELECT co.id,co.name,co.legal_name,co.trading_name,co.country_code,co.is_mro
                FROM company co WHERE co.id=%s AND co.active
                AND EXISTS(SELECT 1 FROM regulatory_approval r WHERE r.company_id=co.id AND {MAINTENANCE_SQL})''',
                (company_id,list(MAINTENANCE_TYPES))).fetchone()
            if company is None:
                raise LookupError('Active maintenance company not found')
            counts = c.execute(f'''SELECT ac.capability_kind,count(*) AS count
                FROM approval_capability ac JOIN regulatory_approval r ON r.id=ac.regulatory_approval_id
                LEFT JOIN company_site s ON s.id=ac.company_site_id
                WHERE r.company_id=%s AND ac.active AND (s.id IS NULL OR s.active) AND {MAINTENANCE_SQL}
                GROUP BY ac.capability_kind''', (company_id,list(MAINTENANCE_TYPES))).fetchall()
            rows = c.execute(f'''SELECT ac.id AS capability_id,ac.regulatory_approval_id,ac.regulatory_approval_id AS approval_id,
                ac.company_site_id,ac.capability_kind,ac.rating_class,ac.rating_code,ac.manufacturer,ac.model,
                ac.aircraft_type_code,ac.limitation,ac.is_base_maintenance,ac.is_line_maintenance,ac.source_code,
                r.authority_code,r.approval_number,r.status AS approval_status,r.valid_from,r.valid_to,
                r.last_verified_at,r.source_url,{CURRENT_SQL} AS approval_current,
                CASE WHEN s.id IS NULL THEN 'COMPANY' ELSE 'SITE' END AS scope_level,
                CASE WHEN s.id IS NULL THEN NULL ELSE jsonb_build_object('id',s.id,'name',s.name,
                    'country_code',s.country_code,'locality',s.locality,'airport_ident',s.airport_ident,
                    'latitude_deg',s.latitude_deg,'longitude_deg',s.longitude_deg,'address_line_1',s.address_line_1) END AS site
                FROM approval_capability ac JOIN regulatory_approval r ON r.id=ac.regulatory_approval_id
                LEFT JOIN company_site s ON s.id=ac.company_site_id
                WHERE r.company_id=%s AND ac.active AND (s.id IS NULL OR s.active) AND {MAINTENANCE_SQL}
                  AND (%s='ALL' OR ac.capability_kind=%s) AND ac.id>%s ORDER BY ac.id LIMIT %s''',
                (company_id,list(MAINTENANCE_TYPES),kind,kind,after,limit+1)).fetchall()
            page = rows[:limit]
            ids = [row['capability_id'] for row in page]
            local = c.execute('SELECT * FROM capability_aircraft_mapping WHERE capability_id=ANY(%s) ORDER BY id', (ids,)).fetchall()
            phrases = list({normalize_phrase(model_phrase(row)) for row in page})
            shared = c.execute('SELECT * FROM capability_phrase_mapping WHERE normalized_phrase=ANY(%s) ORDER BY id', (phrases,)).fetchall()
            for row in page:
                row['mappings'] = (effective_mappings(row, [m for m in local if m['capability_id']==row['capability_id']], shared)
                                   if row['capability_kind']=='AIRCRAFT' else [])
                current = [m for m in row['mappings'] if m['active'] and not m['stale']]
                row['type_interpretation'] = ('SOURCE_CODE' if row['capability_kind']=='AIRCRAFT' and row['aircraft_type_code'] else
                    'REVIEWED_TYPE' if any(m['match_level']=='REVIEWED_TYPE' and not m['variant_scope'] for m in current) else
                    'POSSIBLE_FAMILY' if current else 'UNMAPPED')
                # Internal reviewer identities, notes and stored audit snapshots
                # are not needed by the evidence API's consumers.
                row['mappings'] = [{key:m.get(key) for key in (
                    'aircraft_type_code','match_level','variant_scope','source_url',
                    'active','stale','origin','reviewed_at')} for m in row['mappings']]
            return {'data': {'company':company,'capabilities':page,
                    'kind_counts':{k:next((r['count'] for r in counts if r['capability_kind']==k),0) for k in KINDS}},
                'meta': self._meta(c, next_cursor=str(page[-1]['capability_id']) if len(rows)>limit else None,
                    kind=kind, interpretation='Recorded scope and reviewed type wording; operational suitability is not established')}

    def coverage(self, *, source=None, authority=None, kind='ALL', geography='site_country'):
        source = _code(source, 'source')
        authority = _code(authority, 'authority')
        kind = _choice(kind, (*KINDS, 'ALL'), 'ALL', 'kind')
        geography = str(geography or 'site_country')
        if geography not in ('site_country','company_country'):
            raise ValueError('geography must be site_country or company_country')
        country_sql = 's.country_code' if geography=='site_country' else 'co.country_code'
        # Reproduce local-over-shared precedence and the full staleness snapshot.
        snapshot_sql = "jsonb_build_object(" + ','.join(f"'{field}',ac.{field}" for field in (
            'regulatory_approval_id','company_site_id','capability_kind','rating_class','rating_code',
            'manufacturer','model','aircraft_type_code','limitation','is_base_maintenance','is_line_maintenance')) + ')'
        cte = f'''WITH eligible AS MATERIALIZED (
            SELECT ac.*,r.company_id,r.id AS approval_id,r.authority_code,{CURRENT_SQL} AS approval_current,
                {country_sql} AS country_code,s.airport_ident,s.latitude_deg,s.longitude_deg,
                {snapshot_sql} AS evidence,
                lower(regexp_replace(btrim(coalesce(nullif(btrim(ac.model),''),ac.limitation,'')),'\\s+',' ','g')) AS phrase
            FROM approval_capability ac JOIN regulatory_approval r ON r.id=ac.regulatory_approval_id
            JOIN company co ON co.id=r.company_id LEFT JOIN company_site s ON s.id=ac.company_site_id
            WHERE ac.active AND co.active AND (s.id IS NULL OR s.active) AND {MAINTENANCE_SQL}
              AND (%s::text IS NULL OR ac.source_code=%s) AND (%s::text IS NULL OR r.authority_code=%s)
              AND (%s='ALL' OR ac.capability_kind=%s)
        ), types AS MATERIALIZED (
            SELECT e.id,upper(btrim(e.aircraft_type_code)) AS code FROM eligible e
                WHERE e.capability_kind='AIRCRAFT' AND upper(btrim(e.aircraft_type_code))~'^[A-Z0-9]{{2,4}}$'
            UNION
            SELECT e.id,m.aircraft_type_code FROM eligible e JOIN capability_aircraft_mapping m ON m.capability_id=e.id
                WHERE e.capability_kind='AIRCRAFT' AND m.active AND m.evidence_snapshot=e.evidence
            UNION
            SELECT e.id,m.aircraft_type_code FROM eligible e JOIN capability_phrase_mapping m ON m.normalized_phrase=e.phrase
                WHERE e.capability_kind='AIRCRAFT' AND m.active AND NOT EXISTS(
                    SELECT 1 FROM capability_aircraft_mapping l WHERE l.capability_id=e.id AND l.aircraft_type_code=m.aircraft_type_code)
        )'''
        params = (list(MAINTENANCE_TYPES),source,source,authority,authority,kind,kind)
        aggregates = '''count(DISTINCT e.company_id) AS companies,count(DISTINCT e.approval_id) AS approvals,
            count(DISTINCT e.approval_id) FILTER(WHERE e.approval_current) AS current_approvals,
            count(DISTINCT e.company_site_id) AS sites,count(DISTINCT e.id) AS capabilities,
            count(DISTINCT e.id) FILTER(WHERE e.capability_kind='AIRCRAFT') AS aircraft_capabilities,
            count(DISTINCT e.id) FILTER(WHERE e.company_site_id IS NOT NULL) AS site_capabilities,
            count(DISTINCT e.id) FILTER(WHERE e.company_site_id IS NULL) AS company_capabilities,
            count(DISTINCT t.code) AS distinct_type_codes,
            count(DISTINCT e.id) FILTER(WHERE e.capability_kind='AIRCRAFT' AND t.code IS NOT NULL) AS mapped_aircraft_capabilities,
            count(DISTINCT e.id) FILTER(WHERE e.capability_kind='AIRCRAFT' AND t.code IS NULL) AS unmapped_aircraft_capabilities,
            count(DISTINCT e.company_site_id) FILTER(WHERE e.latitude_deg IS NOT NULL AND e.longitude_deg IS NOT NULL) AS coordinate_sites,
            count(DISTINCT e.company_site_id) FILTER(WHERE e.airport_ident IS NOT NULL) AS airport_linked_sites'''
        with self._read() as c:
            groups = c.execute(cte+f'''SELECT e.country_code,GROUPING(e.country_code) AS is_total,{aggregates},
                array_agg(DISTINCT e.source_code) AS source_codes,array_agg(DISTINCT e.authority_code) AS authority_codes
                FROM eligible e LEFT JOIN types t ON t.id=e.id GROUP BY GROUPING SETS((e.country_code),())
                ORDER BY e.country_code NULLS LAST''',params).fetchall()
            totals = next(r for r in groups if r['is_total'])
            empty = {k:0 for k in totals if k not in ('country_code','is_total','source_codes','authority_codes')}
            def counts(row):
                return {k:v for k,v in row.items() if k not in ('country_code','is_total','source_codes','authority_codes')}
            countries = [{**counts(r),'country_code':r['country_code'],'source_codes':r['source_codes'],
                          'authority_codes':r['authority_codes']} for r in groups if not r['is_total'] and r['country_code']]
            unlocated = next((counts(r) for r in groups if not r['is_total'] and not r['country_code']),empty)
            sources = c.execute(cte+f'''SELECT e.source_code,ds.name AS source_name,{aggregates},
                array_agg(DISTINCT e.authority_code) AS authority_codes,
                max(r.last_verified_at) AS last_verified_at,
                (SELECT max(b.finished_at) FROM company_import_batch b WHERE b.source_code=e.source_code AND b.status='SUCCEEDED') AS last_imported_at,
                (SELECT max(nullif(b.metadata->>'fetched_at','')) FROM company_import_batch b WHERE b.source_code=e.source_code AND b.status='SUCCEEDED') AS last_fetched_at,
                NULL::date AS snapshot_date
                FROM eligible e LEFT JOIN types t ON t.id=e.id JOIN company_data_source ds ON ds.code=e.source_code
                JOIN regulatory_approval r ON r.id=e.approval_id GROUP BY e.source_code,ds.name ORDER BY ds.name,e.source_code''',params).fetchall()
            return {'data':{'totals':counts(totals),'countries':countries,'sources':sources,'unlocated':unlocated,
                'definitions':{'geography':geography,'coverage':'Imported maintenance scope records; national completeness is unknown.',
                    'country':'Recorded physical site country only; no company-country fallback.' if geography=='site_country' else 'Company domicile; does not locate approved maintenance facilities.',
                    'capabilities':'Distinct scope rows, not distinct aircraft types or approved checks.',
                    'distinct_type_codes':'Source codes and active current reviewed/family interpretations; not verified certifications.',
                    'current_approvals':'Recorded VALID status within recorded validity dates today; freshness is separate.',
                    'unlocated':'Records missing the selected geography, including company-wide scope when site country is selected.',
                    'counts':'Distinct counts are not additive across countries or sources.'}},
                'meta':self._meta(c,filters={'source':source,'authority':authority,'kind':kind,'geography':geography})}
