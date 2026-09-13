"""Pinned bulk community evidence and explicitly reviewed, dated identities."""
import csv
import gzip
import io
import json
import logging
import re
import time
import zlib
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import requests
from psycopg.errors import LockNotAvailable, QueryCanceled
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

REPOSITORY = 'https://github.com/wiedehopf/tar1090-db'
RAW = 'https://raw.githubusercontent.com/wiedehopf/tar1090-db/'
MAX_COMPRESSED = 32 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
MAX_ROWS = 2_000_000
GROUPS = ('ROTORCRAFT', 'FIXED_WING', 'GROUND_VEHICLE', 'OTHER', 'UNKNOWN', 'CONFLICT', 'UNMATCHED')


def expand(blob, limit):
    if len(blob) > MAX_COMPRESSED:
        raise ValueError('Source exceeds the compressed size limit')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as f:
            data = f.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Source exceeds the expanded size limit')
        return data.decode('utf-8')
    except (OSError, EOFError, UnicodeError, zlib.error) as exc:
        raise ValueError('Source is not a complete UTF-8 gzip file') from exc


def aircraft_rows(blob):
    """Match the publisher's QUOTE_NONE/backslash escaping, including trailing ;."""
    seen = set()
    try:
        for line, row in enumerate(csv.reader(io.StringIO(expand(blob, MAX_EXPANDED)),
                delimiter=';', escapechar='\\', quoting=csv.QUOTE_NONE, strict=True), 1):
            if (line > MAX_ROWS or len(row) != 8 or row[-1] != ''
                    or not re.fullmatch('[0-9A-Fa-f]{6}', row[0])
                    or any(len(v) > 1000 or '\x00' in v for v in row)):
                raise ValueError(f'Unexpected aircraft CSV structure at record {line}')
            address = row[0].lower()
            if address in seen:
                raise ValueError(f'Duplicate hex at record {line}')
            seen.add(address)
            code = row[2].strip().upper()
            if code and not re.fullmatch('[A-Z0-9]{2,4}', code):
                raise ValueError(f'Unexpected type code at record {line}')
            yield dict(address=address, registration=row[1].strip().upper() or None,
                       type_code=code or None, flags=row[3], description=row[4].strip() or None)
        if not seen:
            raise ValueError('Aircraft CSV is empty')
    except csv.Error as exc:
        raise ValueError('Malformed aircraft CSV') from exc


def type_reference(blob):
    try:
        types = json.loads(expand(blob, 4 * 1024 * 1024))
    except json.JSONDecodeError as exc:
        raise ValueError('Malformed type reference JSON') from exc
    if not isinstance(types, dict) or not types or len(types) > 20000:
        raise ValueError('Unexpected type reference structure')
    for code, values in types.items():
        if (not re.fullmatch('[A-Z0-9]{2,4}', code) or not isinstance(values, list)
                or len(values) != 3 or any(not isinstance(v, str) or len(v) > 500 for v in values)):
            raise ValueError('Unexpected type reference entry')
    return types


def type_category(code, types):
    # ICAO aircraft description: H helicopter, G gyrocopter, R tilt-rotor;
    # L/A/S landplane/amphibian/seaplane. Special codes need explicit treatment.
    if code in ('GND', 'TWR', 'SERV'):
        return 'GROUND_VEHICLE'
    if code in ('GLID', 'BALL', 'SHIP', 'PARA', 'DRON', 'ZZZZ'):
        return 'OTHER' if code != 'ZZZZ' else 'UNKNOWN'
    description = types.get(code, ['', '', ''])[1]
    if description[:1] in ('H', 'G', 'R'):
        return 'ROTORCRAFT'
    if description[:1] in ('L', 'A', 'S'):
        return 'OTHER' if description[1:2] == '0' else 'FIXED_WING'
    return 'UNKNOWN'


def download_snapshot():
    deadline = time.monotonic() + 60

    def get(url, limit):
        if time.monotonic() > deadline:
            raise ValueError('Source download exceeded one minute')
        try:
            with requests.get(url, stream=True, allow_redirects=False, timeout=(5, 10),
                              headers={'User-Agent': 'Heligent tar1090 source adapter',
                                       'Accept-Encoding': 'identity'}) as r:
                if r.status_code != 200:
                    raise ValueError(f'Source returned HTTP {r.status_code}; retry later')
                data = bytearray()
                for part in r.iter_content(65536):
                    data.extend(part)
                    if len(data) > limit or time.monotonic() > deadline:
                        raise ValueError('Source download exceeded its size or time limit')
                return bytes(data)
        except requests.RequestException as exc:
            raise ValueError('Source download failed; check connectivity and retry') from exc

    def revision(branch):
        try:
            obj = json.loads(get(f'https://api.github.com/repos/wiedehopf/tar1090-db/commits/{branch}', 512 * 1024))
            commit = obj['sha']
            published = datetime.fromisoformat(obj['commit']['committer']['date'].replace('Z', '+00:00'))
            if not re.fullmatch('[0-9a-f]{40}', commit) or published.tzinfo is None:
                raise ValueError()
            return commit, published.isoformat()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Could not verify the upstream revision and publication date') from exc

    csv_ref, published = revision('csv')
    types_ref, types_published = revision('master')
    csv_url, types_url = RAW + csv_ref + '/aircraft.csv.gz', RAW + types_ref + '/db/icao_aircraft_types2.js'
    aircraft, types = get(csv_url, MAX_COMPRESSED), get(types_url, 1024 * 1024)
    metadata = dict(csv_revision=csv_ref, source_date=published, source_url=csv_url,
                    types_revision=types_ref, types_date=types_published, types_url=types_url)
    return aircraft, types, metadata


def refresh_snapshot(store, actor):
    # Session lock survives the durable attempt commits, and releases on disconnect.
    with store.connect() as c:
        c.row_factory = dict_row
        c.execute("SET statement_timeout='20s'")
        c.execute("SET lock_timeout='3s'")
        if not c.execute('SELECT pg_try_advisory_lock(714524) AS locked').fetchone()['locked']:
            raise ValueError('A tar1090 refresh is already running')
        try:
            recent = c.execute("SELECT 1 FROM tar1090_attempt WHERE started_at>now()-interval '1 minute' LIMIT 1").fetchone()
            if recent:
                raise ValueError('Allow one minute between source refreshes')
            c.execute("UPDATE tar1090_attempt SET status='FAILED',finished_at=now(),error_message='Previous refresh was interrupted' WHERE status='RUNNING'")
            attempt = c.execute("INSERT INTO tar1090_attempt(status,requested_by) VALUES ('RUNNING',%s) RETURNING id", (actor,)).fetchone()['id']
            c.commit()
            try:
                aircraft, type_blob, meta = download_snapshot()
                types = type_reference(type_blob)
                counts = Counter(rows=0, hex=0, tail=0, icao_type=0, category=0, rotorcraft=0)
                for row in aircraft_rows(aircraft):
                    category = type_category(row['type_code'], types)
                    counts.update(rows=1, hex=1, tail=bool(row['registration']), icao_type=bool(row['type_code']),
                                  category=category != 'UNKNOWN', rotorcraft=category == 'ROTORCRAFT')
                # Fail closed on an unexpectedly small upstream export, even if syntactically valid.
                if counts['rows'] < 100000 or len(types) < 1000:
                    raise ValueError('Source export is unexpectedly small; previous snapshot retained')
                meta.update(sha256=sha256(aircraft).hexdigest(), types_sha256=sha256(type_blob).hexdigest(),
                            source_bytes=len(aircraft), field_coverage=dict(counts), type_count=len(types),
                            attribution='tar1090-db / wiedehopf; Mictronics aircraft database; ADSB Exchange')
                fingerprint = sha256(json.dumps(meta, sort_keys=True).encode()).hexdigest()
                row = c.execute('''INSERT INTO tar1090_snapshot(fingerprint,source_date,metadata,aircraft_gzip,types_gzip)
                    VALUES (%s,%s,%s,%s,%s) ON CONFLICT(fingerprint) DO NOTHING RETURNING id''',
                    (fingerprint, meta['source_date'], Jsonb(meta), aircraft, type_blob)).fetchone()
                status = 'IMPORTED' if row else 'UNCHANGED'
                if row is None:
                    row = c.execute('SELECT id FROM tar1090_snapshot WHERE fingerprint=%s', (fingerprint,)).fetchone()
                c.execute('UPDATE tar1090_attempt SET status=%s,snapshot_id=%s,finished_at=now() WHERE id=%s', (status, row['id'], attempt))
                c.commit()
                return dict(status=status, snapshot_id=row['id'], metadata=meta)
            except Exception as exc:
                c.rollback()
                if not isinstance(exc, ValueError):
                    logging.getLogger(__name__).exception('tar1090 snapshot refresh failed')
                message = str(exc) if isinstance(exc, ValueError) else 'Refresh failed; previous snapshot retained. Check server logs.'
                c.execute("UPDATE tar1090_attempt SET status='FAILED',finished_at=now(),error_message=%s WHERE id=%s", (message[:500], attempt))
                c.commit()
                if isinstance(exc, ValueError):
                    raise
                raise ValueError(message) from exc
        finally:
            c.rollback()
            c.execute('SELECT pg_advisory_unlock(714524)')


def candidate(observed, claim, types, curated):
    result = dict(observed, claim=claim, conflicts=[])
    if claim is None:
        return dict(result, category='UNKNOWN', group='UNMATCHED', reviewable=False)
    code = claim['type_code']
    upstream_category = type_category(code, types)
    local_category = curated.get(code, 'UNKNOWN')
    if local_category not in GROUPS:
        local_category = 'OTHER'
    category = local_category if local_category != 'UNKNOWN' else upstream_category
    if category not in GROUPS:
        category = 'OTHER'
    conflicts = result['conflicts']
    if local_category != 'UNKNOWN' and upstream_category != 'UNKNOWN' and local_category != upstream_category:
        conflicts.append('Type category differs from the local reference')
    norm = lambda s: s.replace('-', '').strip().upper()
    if claim['registration'] and any(norm(r) != norm(claim['registration']) for r in observed['registrations']):
        conflicts.append('Existing resolved tail differs')
    if code and any(t.upper() != code for t in observed['types']):
        conflicts.append('Existing resolved type differs')
    if category != 'UNKNOWN' and any((c if c in GROUPS else 'OTHER') not in ('UNKNOWN', category) for c in observed['categories']):
        conflicts.append('Existing aircraft category differs')
    valid_tail = bool(claim['registration'] and re.fullmatch('[A-Z0-9]{1,3}-?[A-Z0-9]{1,6}', claim['registration']))
    fills_gap = bool(claim['registration'] and observed['missing_tail'] or code and observed['missing_type'])
    return dict(result, category=category, group='CONFLICT' if conflicts else category,
                reviewable=valid_tail and fills_gap and not conflicts and category not in ('GROUND_VEHICLE', 'OTHER'))


def build_preview(store, payload, actor):
    if not isinstance(payload, dict):
        raise ValueError('Expected preview filters')
    region, gap = payload.get('region', 'EU'), payload.get('gap', 'TAIL')
    if region not in ('ALL', 'EU', 'GB', 'NA', 'SA', 'AF', 'AS', 'OC', 'AN', 'UNLOCATED') or gap not in ('TAIL', 'TYPE', 'EITHER', 'BOTH'):
        raise ValueError('Invalid region or identity gap')
    with store.connect() as c:
        c.row_factory = dict_row
        c.execute("SET LOCAL statement_timeout='20s'")
        snapshot = c.execute('SELECT * FROM tar1090_snapshot ORDER BY source_date DESC,id DESC LIMIT 1').fetchone()
        if not snapshot:
            raise ValueError('Download a tar1090 reference snapshot first')
        latest = c.execute("SELECT max(utc_date) AS day FROM dataset_day WHERE status='PROCESSED'").fetchone()['day']
        end = date.fromisoformat(str(payload['to'])) if payload.get('to') else latest or date.today()
        start = date.fromisoformat(str(payload['from'])) if payload.get('from') else end - timedelta(days=6)
        if end < start or (end-start).days > 30:
            raise ValueError('Choose an ordered range of at most 31 days')
        days = c.execute("SELECT count(*) AS days FROM dataset_day WHERE status='PROCESSED' AND utc_date BETWEEN %s AND %s", (start, end)).fetchone()['days']
        # Include all observed days for each selected hex when checking conflicts,
        # including days that already have metadata. EXISTS avoids multiplying visits.
        observed = c.execute('''SELECT d.address,min(d.utc_date) AS first_day,max(d.utc_date) AS last_day,
            count(*) AS days,sum(d.airborne_time_seconds)/3600.0 AS hours,
            bool_or(nullif(btrim(d.registration),'') IS NULL) AS missing_tail,
            bool_or(nullif(btrim(d.type_code),'') IS NULL) AS missing_type,
            bool_or(nullif(btrim(d.registration),'') IS NULL AND nullif(btrim(d.type_code),'') IS NULL) AS missing_both,
            array_remove(array_agg(DISTINCT nullif(btrim(d.registration),'')),NULL) AS registrations,
            array_remove(array_agg(DISTINCT nullif(btrim(d.type_code),'')),NULL) AS types,
            array_agg(DISTINCT d.resolved_category::text) AS categories,
            array_remove(array_agg(DISTINCT d.identity_source_code),NULL) AS identity_sources
            FROM aircraft_day_identity d JOIN dataset_day q ON q.id=d.dataset_day_id
            WHERE q.status='PROCESSED' AND d.utc_date BETWEEN %s AND %s AND d.position_count>0
            GROUP BY d.address HAVING bool_or(%s='ALL' OR EXISTS (
                SELECT 1 FROM aircraft_airport_visit v JOIN airport ap ON ap.ident=v.airport_ident
                WHERE v.dataset_day_id=d.dataset_day_id AND v.address=d.address
                AND (ap.continent=%s OR (%s='GB' AND ap.iso_country='GB')))
                OR (%s='UNLOCATED' AND NOT EXISTS (
                    SELECT 1 FROM aircraft_airport_visit v JOIN airport ap ON ap.ident=v.airport_ident
                    WHERE v.dataset_day_id=d.dataset_day_id AND v.address=d.address AND ap.continent IS NOT NULL)))
            ORDER BY d.address LIMIT 100001''', (start, end, region, region, region, region)).fetchall()
        if len(observed) > 100000:
            raise ValueError('More than 100,000 observed hexes; narrow the date range or region')
        curated = {r['type_code']: r['category'] for r in c.execute('SELECT type_code,category::text FROM aircraft_type_classification')}
    observed = [r for r in observed if {'TAIL': r['missing_tail'], 'TYPE': r['missing_type'],
                'EITHER': r['missing_tail'] or r['missing_type'], 'BOTH': r['missing_both']}[gap]]
    wanted = {r['address'] for r in observed}
    claims = {r['address']: r for r in aircraft_rows(bytes(snapshot['aircraft_gzip'])) if r['address'] in wanted}
    types = type_reference(bytes(snapshot['types_gzip']))
    rows = [candidate(r, claims.get(r['address']), types, curated) for r in observed]
    rows.sort(key=lambda r: (GROUPS.index(r['group']), -float(r['hours'] or 0), r['address']))
    counts = dict(Counter(r['group'] for r in rows))
    report = dict(rows=rows, counts=counts, total=len(rows), matched=len(claims),
                  reviewable=sum(r['reviewable'] for r in rows), region=region, gap=gap,
                  **{'from': start, 'to': end}, processed_days=days, expected_days=(end-start).days+1,
                  snapshot_id=snapshot['id'], metadata=snapshot['metadata'], created_at=datetime.now(UTC))
    # Retain the comparison the analyst saw. Save-time identity checks remain live.
    report = json.loads(json.dumps(report, default=lambda v: float(v) if hasattr(v, 'as_tuple') else str(v)))
    preview_id = uuid4()
    with store.connect() as c:
        c.execute('INSERT INTO tar1090_preview(id,snapshot_id,report,created_by) VALUES (%s,%s,%s,%s)',
                  (preview_id, snapshot['id'], Jsonb(report), actor))
    return page(report, str(preview_id), {})


def page(report, preview_id, params):
    group, query = params.get('group', 'ROTORCRAFT'), params.get('search', '').strip().upper()
    offset = int(params.get('offset', 0))
    if group not in ('ALL', *GROUPS) or len(query) > 40 or not 0 <= offset <= 100000:
        raise ValueError('Invalid preview page filters')
    rows = [r for r in report['rows'] if (group == 'ALL' or r['group'] == group)
            and query in ' '.join([r['address'].upper(), (r.get('claim') or {}).get('registration') or '',
                                  (r.get('claim') or {}).get('type_code') or ''])]
    return {**report, 'preview_id': preview_id, 'rows': rows[offset:offset+50],
            'eligible_addresses': [r['address'] for r in rows if r['reviewable']],
            'filtered_count': len(rows), 'offset': offset, 'has_more': len(rows) > offset+50, 'group': group}


def load_preview(store, preview_id):
    try:
        key = UUID(str(preview_id))
    except ValueError as exc:
        raise ValueError('Invalid saved preview') from exc
    with store.connect() as c:
        row = c.execute('SELECT report FROM tar1090_preview WHERE id=%s', (key,)).fetchone()
    if not row:
        raise ValueError('Saved preview was not found; run a new comparison')
    return row[0]


def preview_page(store, preview_id, params):
    report = load_preview(store, preview_id)
    with store.connect() as c:
        applied = {r[0]:r[1] for r in c.execute('''SELECT address,
            assignment->'evidence'->>'mode' FROM aircraft_identity_review
            WHERE assignment->'evidence'->>'preview_id'=%s''', (preview_id,))}
    for row in report['rows']:
        if row['address'] in applied:
            row.update(applied=True, reviewable=False, applied_mode=applied[row['address']])
    return page(report, preview_id, params)


def review_identity(store, payload, actor, save=False):
    from .identity import assign_identity
    if not isinstance(payload, dict) or not payload.get('valid_to') or not str(payload.get('notes', '')).strip():
        raise ValueError('Provide both verified effective dates and a note explaining your dated evidence')
    report = load_preview(store, payload.get('preview_id'))
    row = next((r for r in report['rows'] if r['address'] == str(payload.get('address', '')).lower()), None)
    if not row or not row['reviewable']:
        raise ValueError('This candidate is not eligible for assignment; review its gaps or conflicts separately')
    claim, meta = row['claim'], report['metadata']
    evidence = identity_evidence(report, row, payload['preview_id'])
    # Tail/type/source fields are taken only from retained evidence, never the client.
    p = dict(address=row['address'], registration=claim['registration'], type_code=claim['type_code'],
             valid_from=payload.get('valid_from'), valid_to=payload['valid_to'], notes=payload['notes'],
             source_url=meta['source_url'], token=payload.get('token'))
    return assign_identity(store, p, actor, save=save, evidence=evidence)


def identity_evidence(report, row, preview_id):
    meta = report['metadata']
    return dict(source='TAR1090_DB', snapshot_id=report['snapshot_id'], preview_id=preview_id,
                    sha256=meta['sha256'], csv_revision=meta['csv_revision'], source_date=meta['source_date'],
                    types_sha256=meta['types_sha256'], types_revision=meta['types_revision'],
                    category=row['category'], claim=row['claim'])


def fill_identity(store, payload, actor):
    """One atomic item of the UI's bulk action; interruption/retry is safe."""
    from .identity import assign_identity
    if not isinstance(payload, dict) or type(payload.get('add_watch', True)) is not bool:
        raise ValueError('Expected a source candidate and watchlist option')
    report = load_preview(store, payload.get('preview_id'))
    address = str(payload.get('address', '')).lower()
    row = next((r for r in report['rows'] if r['address']==address), None)
    if not row or not row['reviewable']:
        return dict(address=address,status='SKIPPED',reason='No eligible source match; conflicting or incomplete identity')
    claim = row['claim']
    evidence = identity_evidence(report,row,payload['preview_id']) | {'mode':'BULK_PROVISIONAL'}
    p = dict(address=address, registration=claim['registration'], type_code=claim['type_code'],
             valid_from=report['from'], valid_to=report['to'], source_url=report['metadata']['source_url'],
             notes='Provisional tar1090 fill for internal Maintenance Pulse testing. Dates use the comparison period; effective assignment dates have not been independently verified.')
    try:
        result = assign_identity(store,p,actor,save=True,evidence=evidence,provisional=True,
                                 add_watch=payload.get('add_watch',True) and row['category']=='ROTORCRAFT')
        return dict(result,address=address,registration=claim['registration'],
                    status='ALREADY_APPLIED' if result['already_applied'] else 'FILLED')
    except ValueError as exc:
        return dict(address=address,status='SKIPPED',reason=str(exc))
    except (QueryCanceled,LockNotAvailable):
        return dict(address=address,status='FAILED',reason='Database was busy; retry this candidate')
