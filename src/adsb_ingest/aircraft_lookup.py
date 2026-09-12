"""Bounded, cached, user-requested lookups. Never a bulk enrichment worker."""
from datetime import UTC, datetime
import json
import re
import time

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import requests

ENDPOINTS = {'ADSBDB': 'https://api.adsbdb.com/v0/aircraft/',
             'HEXDB': 'https://hexdb.io/api/v1/aircraft/'}


def clean(value, limit=200):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value and value.upper() not in ('UNKNOWN', 'N/A', 'NULL', '-') else None


def normalize_response(provider, address, payload):
    if not isinstance(payload, dict):
        raise ValueError('Provider returned an unexpected response')
    if provider == 'ADSBDB':
        if payload.get('response') == 'unknown aircraft':
            return {'status': 'NOT_FOUND'}
        wrapper = payload.get('response')
        row = wrapper.get('aircraft') if isinstance(wrapper, dict) else None
        keys = ('mode_s', 'registration', 'icao_type', 'type', 'manufacturer')
    else:
        if str(payload.get('status')) == '404':
            return {'status': 'NOT_FOUND'}
        row = payload
        keys = ('ModeS', 'Registration', 'ICAOTypeCode', 'Type', 'Manufacturer')
    if not isinstance(row, dict) or str(row.get(keys[0], '')).lower() != address:
        raise ValueError('Provider returned a different or missing hex address')
    values = dict(zip(('address', 'registration', 'type_code', 'model', 'manufacturer'),
                      (clean(row.get(k)) for k in keys)))
    values['address'] = address
    for key, pattern in (('registration', r'[A-Z0-9][A-Z0-9-]{0,19}'), ('type_code', r'[A-Z0-9]{2,4}')):
        value = values[key]
        values[key] = value.upper() if value and re.fullmatch(pattern, value.upper()) else None
    return {'status': 'FOUND', **values}


def fetch_aircraft(provider, address):
    url = ENDPOINTS[provider] + address
    try:
        started = time.monotonic()
        with requests.get(url, timeout=(3, 8), stream=True, allow_redirects=False,
                          headers={'User-Agent': 'Heligent/0.10 aircraft-review', 'Accept': 'application/json'}) as response:
            if response.status_code == 404:
                return {'status': 'NOT_FOUND'}
            if response.status_code != 200:
                return {'status': 'FAILED', 'error': f'Provider returned HTTP {response.status_code}; no identity changed.'}
            content = bytearray()
            for chunk in response.iter_content(8192):
                content.extend(chunk)
                if len(content) > 128 * 1024 or time.monotonic() - started > 15:
                    raise ValueError('Provider response exceeded the lookup limit')
            return normalize_response(provider, address, json.loads(content))
    except (requests.RequestException, ValueError) as exc:
        # Do not expose connection details or arbitrary upstream HTML.
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else 'Provider could not be reached or returned invalid JSON'
        return {'status': 'FAILED', 'error': message[:250]}


def lookup_aircraft(store, provider, address, actor):
    if provider not in ENDPOINTS or not isinstance(address, str) or not re.fullmatch(r'[0-9A-Fa-f]{6}', address.strip()):
        raise ValueError('Choose ADSBdb or HexDB and enter exactly six hexadecimal characters')
    address = address.strip().lower()
    with store.connect() as c:
        c.row_factory = dict_row
        c.execute("SET LOCAL statement_timeout='5s'")
        if not c.execute('SELECT pg_try_advisory_xact_lock(%s) AS locked',
                         (714522 if provider == 'ADSBDB' else 714523,)).fetchone()['locked']:
            raise ValueError('Another lookup is running for this source. Try again shortly.')
        previous = c.execute('''SELECT * FROM tool_source_lookup WHERE source_code=%s AND address=%s
            AND fetched_at > now() - CASE WHEN status='FAILED' THEN interval '5 minutes' ELSE interval '30 days' END
            ORDER BY fetched_at DESC LIMIT 1''', (provider, address)).fetchone()
        if previous:
            return {**previous, 'cached': True}
        allowance = c.execute('''SELECT count(*) AS daily,
            count(*) FILTER (WHERE fetched_at>now()-interval '5 seconds') AS recent
            FROM tool_source_lookup WHERE source_code=%s AND fetched_at>now()-interval '1 day' ''', (provider,)).fetchone()
        if allowance['daily'] >= 60 or allowance['recent']:
            raise ValueError('Lookup limit reached: allow five seconds between requests; maximum 60 new hex lookups per source per day.')
        result = fetch_aircraft(provider, address)
        if result.get('type_code'):
            classification = c.execute('SELECT category::text FROM aircraft_type_classification WHERE type_code=%s',
                                       (result['type_code'],)).fetchone()
            result['category'] = classification['category'] if classification else 'UNKNOWN'
        row = c.execute('''INSERT INTO tool_source_lookup(source_code,address,status,fetched_at,source_url,result,requested_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (provider, address, result['status'], datetime.now(UTC), ENDPOINTS[provider]+address, Jsonb(result), actor)).fetchone()
    return {**row, 'cached': False}
