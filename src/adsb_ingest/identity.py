"""Explicit, audited identity enrichment; never infer a registration from callsigns."""
from datetime import date
from hashlib import sha256
import json
import re
from urllib.parse import urlparse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError('Expected a JSON object')
    address = str(payload.get('address','')).strip().lower()
    registration = str(payload.get('registration','')).strip().upper()
    code = str(payload.get('type_code') or '').strip().upper() or None
    if not re.fullmatch('[0-9a-f]{6}', address):
        raise ValueError('Assignments require a six-character ICAO hex address')
    if not re.fullmatch('[A-Z0-9]{1,3}-?[A-Z0-9]{1,6}', registration):
        raise ValueError('Enter a registration using letters, numbers and an optional hyphen')
    if code and not re.fullmatch('[A-Z0-9]{2,4}',code):
        raise ValueError('Enter a 2–4 character ICAO type code, or leave it blank')
    start = date.fromisoformat(str(payload.get('valid_from','')))
    end = date.fromisoformat(str(payload['valid_to'])) if payload.get('valid_to') else None
    if end and end < start:
        raise ValueError('End date must not precede start date')
    source = str(payload.get('source_url','')).strip()
    url = urlparse(source)
    notes = str(payload.get('notes','')).strip()
    if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or len(source)>2000:
        raise ValueError('Provide an HTTP(S) evidence URL without credentials')
    if not notes or len(notes)>4000:
        raise ValueError('Provide evidence notes (maximum 4000 characters)')
    return dict(address=address,registration=registration,type_code=code,valid_from=start,valid_to=end,source_url=source,notes=notes)


def assign_identity(store, payload, actor, save=False):
    p = validate(payload)
    address,start,end = p['address'],p['valid_from'],p['valid_to']
    with store.connect() as c:
        c.row_factory = dict_row
        c.execute("SET LOCAL statement_timeout = '15s'")
        c.execute("SET LOCAL lock_timeout = '3s'")
        if save:
            c.execute('SELECT pg_advisory_xact_lock(714519)')
        aircraft = c.execute('SELECT address,registration,type_code,type_description FROM aircraft WHERE address=%s'+(' FOR UPDATE' if save else ''),(address,)).fetchone()
        if not aircraft:
            raise ValueError('Hex address is not present in the catalogue')
        latest = c.execute('SELECT max(utc_date) AS day FROM aircraft_day WHERE address=%s',(address,)).fetchone()['day']
        if latest and latest >= start and (end is None or latest <= end):
            current = (aircraft['registration'] or '').strip().upper().replace('-','')
            if current and current != p['registration'].replace('-',''):
                raise ValueError('The current aircraft registration conflicts with this assignment; review identity first')
        overlaps = c.execute('''SELECT 1 FROM aircraft_metadata_override WHERE
            (address=%s OR replace(upper(registration),'-','')=%s)
            AND (valid_to IS NULL OR valid_to >= %s) AND (%s::date IS NULL OR valid_from <= %s) LIMIT 1''',
            (address,p['registration'].replace('-',''),start,end,end)).fetchone()
        if overlaps:
            raise ValueError('An overlapping identity override exists for this hex or registration; review it before assigning')
        rows = c.execute('''SELECT dataset_day_id,utc_date,address,registration,type_code,type_description
            FROM aircraft_day WHERE address=%s AND utc_date >= %s AND (%s::date IS NULL OR utc_date <= %s)
            ORDER BY utc_date,dataset_day_id LIMIT 10001'''+(' FOR UPDATE' if save else ''),(address,start,end,end)).fetchall()
        if not rows or len(rows)>10000:
            raise ValueError('Choose a range containing 1–10000 daily records')
        norm = p['registration'].replace('-','')
        if any(r['registration'] and r['registration'].strip() and r['registration'].upper().replace('-','').strip()!=norm for r in rows):
            raise ValueError('This range already contains another registration; narrow the dates or review the conflict')
        if p['type_code'] and any(r['type_code'] and r['type_code'].strip() and r['type_code'].strip().upper()!=p['type_code'] for r in rows):
            raise ValueError('Existing aircraft types conflict with the proposed type')
        other = c.execute('''SELECT 1 FROM aircraft_day WHERE replace(upper(registration),'-','')=%s
            AND address<>%s AND utc_date >= %s AND (%s::date IS NULL OR utc_date<=%s) LIMIT 1''',(norm,address,start,end,end)).fetchone()
        if other:
            raise ValueError('This registration has observations under another hex in the chosen range; review identity first')
        fingerprint = sha256(json.dumps([p,rows,aircraft],sort_keys=True,default=str).encode()).hexdigest()
        if not save:
            return dict(token=fingerprint,affected_days=len(rows),first_day=rows[0]['utc_date'],last_day=rows[-1]['utc_date'],assignment=p)
        if payload.get('token') != fingerprint:
            raise ValueError('Assignment or underlying records changed. Preview again before saving.')
        c.execute('INSERT INTO aircraft_identity_review(address,assignment,previous_rows,previous_aircraft,reviewed_by) VALUES (%s,%s,%s,%s,%s)',
            (address,Jsonb(json.loads(json.dumps(p,default=str))),Jsonb(json.loads(json.dumps(rows,default=str))),Jsonb(aircraft),actor))
        c.execute('''INSERT INTO aircraft_metadata_override(address,valid_from,valid_to,registration,type_code,source_url,notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s)''',(address,start,end,p['registration'],p['type_code'],p['source_url'],p['notes']))
        # Existing trigger applies the dated override to each historical row.
        c.execute('''UPDATE aircraft_day SET registration=registration WHERE address=%s
            AND utc_date>=%s AND (%s::date IS NULL OR utc_date<=%s)''',(address,start,end,end))
        # Do not leave a historical assignment on the current aircraft record.
        latest = c.execute('SELECT max(utc_date) AS day FROM aircraft_day WHERE address=%s',(address,)).fetchone()['day']
        if latest < start or (end and latest > end):
            c.execute('UPDATE aircraft SET registration=%s,type_code=%s,type_description=%s WHERE address=%s',
                (aircraft['registration'],aircraft['type_code'],aircraft['type_description'],address))
    return {'saved':True,'affected_days':len(rows),'message':'Identity saved. Reload the Stops map. Cached analytics and Sproutt reports require their normal refresh/sync.'}
