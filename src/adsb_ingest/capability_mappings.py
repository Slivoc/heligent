"""Audited interpretations of a particular imported capability, not global aliases."""
import re
from urllib.parse import urlparse

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


SCOPE_FIELDS = ('regulatory_approval_id', 'company_site_id', 'capability_kind',
                'rating_class', 'rating_code', 'manufacturer', 'model',
                'aircraft_type_code', 'limitation', 'is_base_maintenance', 'is_line_maintenance')


def evidence_snapshot(capability):
    return {key: capability.get(key) for key in SCOPE_FIELDS}


def mapping_input(payload):
    if not isinstance(payload, dict):
        raise ValueError('Mapping must be a JSON object')
    code = str(payload.get('aircraft_type_code') or '').strip().upper()
    level = payload.get('match_level')
    variant = str(payload.get('variant_scope') or '').strip()
    notes = str(payload.get('notes') or '').strip()
    source = str(payload.get('source_url') or '').strip()
    parsed = urlparse(source)
    revision = payload.get('revision')
    active = payload.get('active', True)
    if not re.fullmatch(r'[A-Z0-9]{2,4}', code):
        raise ValueError('Use a 2–4 character ICAO type code, e.g. EC45')
    if level not in ('POSSIBLE_FAMILY', 'REVIEWED_TYPE'):
        raise ValueError('Choose a possible family or reviewed type mapping')
    if not notes or len(notes) > 4000 or len(variant) > 1000:
        raise ValueError('Evidence notes are required (max 4000 characters); variant scope max 1000')
    if len(source) > 2000 or parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Provide an HTTP(S) evidence URL without embedded credentials')
    if type(revision) is not int or revision < 0 or type(active) is not bool:
        raise ValueError('Provide the current revision (0 for new) and a boolean active flag')
    if level == 'REVIEWED_TYPE' and not variant and payload.get('type_wide_confirmed') is not True:
        raise ValueError('Explicitly confirm type-wide scope, or enter the restricted variants')
    return code, level, variant, source, notes, revision, active


class CapabilityMappingStore:
    def __init__(self, store):
        self.store = store

    def search(self, query='', offset=0):
        query = str(query).strip()
        if len(query) > 200 or offset < 0 or offset > 100000:
            raise ValueError('Invalid search or page offset')
        pattern = '%' + query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
        with self.store.connect() as c:
            c.row_factory = dict_row
            c.execute("SET LOCAL statement_timeout = '10s'")
            rows = c.execute('''SELECT ac.id, co.name AS company_name, s.name AS site_name,
                r.approval_number,r.status AS approval_status,ac.model,ac.aircraft_type_code,ac.limitation,
                (SELECT count(*) FROM capability_aircraft_mapping m WHERE m.capability_id=ac.id AND m.active) AS mapping_count
                FROM approval_capability ac JOIN regulatory_approval r ON r.id=ac.regulatory_approval_id
                JOIN company co ON co.id=r.company_id LEFT JOIN company_site s ON s.id=ac.company_site_id
                WHERE ac.active AND co.active AND ac.capability_kind='AIRCRAFT'
                  AND (s.id IS NULL OR s.active)
                  AND concat_ws(' ',co.name,s.name,r.approval_number,ac.model,ac.aircraft_type_code,ac.limitation) ILIKE %s
                ORDER BY co.name,ac.id LIMIT 51 OFFSET %s''', (pattern,offset)).fetchall()
            return {'rows':rows[:50], 'has_more':len(rows)>50, 'offset':offset}

    @staticmethod
    def _capability(c, capability_id, lock=False):
        cap = c.execute('''SELECT ac.*, co.name AS company_name, co.active AS company_active,
            s.name AS site_name,s.active AS site_active,r.approval_number,r.status AS approval_status,
            r.valid_from,r.valid_to,r.source_url AS approval_source_url
            FROM approval_capability ac JOIN regulatory_approval r ON r.id=ac.regulatory_approval_id
            JOIN company co ON co.id=r.company_id LEFT JOIN company_site s ON s.id=ac.company_site_id
            WHERE ac.id=%s''' + (' FOR UPDATE OF ac' if lock else ''), (capability_id,)).fetchone()
        if cap is None:
            raise ValueError('Capability not found')
        return cap

    def detail(self, capability_id):
        with self.store.connect() as c:
            c.row_factory = dict_row
            c.execute("SET LOCAL statement_timeout = '10s'")
            cap = self._capability(c, capability_id)
            rows = c.execute('SELECT * FROM capability_aircraft_mapping WHERE capability_id=%s ORDER BY aircraft_type_code', (capability_id,)).fetchall()
            for row in rows:
                row['stale'] = row['evidence_snapshot'] != evidence_snapshot(cap)
            history = c.execute('''SELECT h.snapshot,h.recorded_at FROM capability_mapping_revision h
                JOIN capability_aircraft_mapping m ON m.id=h.mapping_id
                WHERE m.capability_id=%s ORDER BY h.id DESC LIMIT 101''', (capability_id,)).fetchall()
            return {'capability':cap,'source_snapshot':evidence_snapshot(cap),
                    'mappings':rows,'history':history[:100],'history_truncated':len(history)>100}

    def save(self, capability_id, payload, actor):
        code,level,variant,source,notes,revision,active = mapping_input(payload)
        with self.store.connect() as c:
            c.row_factory = dict_row
            c.execute("SET LOCAL statement_timeout = '10s'")
            # Parent lock serializes even first inserts and coordinates with source imports.
            cap = self._capability(c, capability_id, lock=True)
            if cap['capability_kind'] != 'AIRCRAFT' or not cap['active'] or not cap['company_active'] or cap['site_active'] is False:
                raise ValueError('Only active aircraft capabilities can be mapped')
            if payload.get('evidence_snapshot') != evidence_snapshot(cap):
                raise ValueError('Imported capability changed; reload and review the new wording')
            old = c.execute('SELECT * FROM capability_aircraft_mapping WHERE capability_id=%s AND aircraft_type_code=%s FOR UPDATE', (capability_id,code)).fetchone()
            if revision != (old['revision'] if old else 0):
                raise ValueError('Mapping changed since it was opened; reload before saving')
            if old:
                c.execute('INSERT INTO capability_mapping_revision(mapping_id,snapshot) SELECT id,to_jsonb(m) FROM capability_aircraft_mapping m WHERE id=%s', (old['id'],))
            return c.execute('''INSERT INTO capability_aircraft_mapping
                (capability_id,aircraft_type_code,match_level,variant_scope,source_url,notes,evidence_snapshot,active,reviewed_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(capability_id,aircraft_type_code) DO UPDATE SET
                match_level=excluded.match_level,variant_scope=excluded.variant_scope,
                source_url=excluded.source_url,notes=excluded.notes,evidence_snapshot=excluded.evidence_snapshot,
                active=excluded.active,reviewed_by=excluded.reviewed_by,reviewed_at=now(),
                revision=capability_aircraft_mapping.revision+1 RETURNING *''',
                (capability_id,code,level,variant,source,notes,Jsonb(evidence_snapshot(cap)),active,actor)).fetchone()
