"""Bounded, authenticated map reads. No downloads, geocoding or raw rebuilds."""
from datetime import date, timedelta

from psycopg.rows import dict_row


def date_window(start, end, latest):
    end = date.fromisoformat(end) if end else (latest or date.today())
    start = date.fromisoformat(start) if start else end - timedelta(days=6)
    if end < start or (end - start).days > 30:
        raise ValueError('Choose an ordered UTC date range of at most 31 days')
    return start, end


def capability_match(cap, type_code, today):
    """Exact ICAO code only: no model-substring guesses or approval inference."""
    exact = (bool(type_code) and cap.get('capability_kind') == 'AIRCRAFT'
             and str(cap.get('aircraft_type_code') or '').strip().upper() == type_code.strip().upper())
    valid = (cap['approval_status'] == 'VALID'
             and (not cap.get('valid_from') or cap['valid_from'] <= today)
             and (not cap.get('valid_to') or cap['valid_to'] >= today))
    if not exact:
        return 'NO_RECORDED_MATCH'
    if not valid:
        return 'APPROVAL_NOT_CURRENT'
    return 'SITE_MATCH' if cap.get('company_site_id') is not None else 'COMPANY_MATCH'


def map_data(store, watch_id, start=None, end=None):
    with store.connect() as c:
        c.row_factory = dict_row
        c.execute("SET LOCAL statement_timeout = '15s'")
        watch = c.execute('''SELECT w.* FROM maintenance_watch w
            JOIN maintenance_watchlist l ON l.id=w.watchlist_id
            WHERE w.id=%s AND l.scope_key='internal' ''', (watch_id,)).fetchone()
        if watch is None:
            raise ValueError('Watch not found')
        latest = c.execute("SELECT max(utc_date) AS day FROM dataset_day WHERE status='PROCESSED'").fetchone()['day']
        start, end = date_window(start, end, latest)
        tail = watch['registration']
        # Match the registration recorded on that historical date, not today's owner.
        days = c.execute('''SELECT d.utc_date, d.address, d.type_code, d.type_description,
                d.position_count, q.derivation_version
            FROM aircraft_day d JOIN dataset_day q ON q.id=d.dataset_day_id
            WHERE replace(upper(d.registration),'-','')=%s AND d.utc_date BETWEEN %s AND %s
            ORDER BY d.utc_date DESC, d.address''', (tail,start,end)).fetchall()
        codes = sorted({d['type_code'].strip().upper() for d in days if d['type_code']})
        # An inconsistent type is never silently resolved for capability highlighting.
        type_code = codes[0] if len(codes) == 1 else None
        visits = c.execute('''SELECT v.*, a.name AS airport_name,
                a.latitude_deg, a.longitude_deg
            FROM aircraft_day d JOIN aircraft_airport_visit v
              ON v.dataset_day_id=d.dataset_day_id AND v.address=d.address
            JOIN airport a ON a.ident=v.airport_ident
            WHERE replace(upper(d.registration),'-','')=%s AND d.utc_date BETWEEN %s AND %s
            ORDER BY v.first_evidence_at DESC, v.address, v.visit_sequence LIMIT 501''', (tail,start,end)).fetchall()
        processed = c.execute('''SELECT utc_date, derivation_version FROM dataset_day
            WHERE status='PROCESSED' AND utc_date BETWEEN %s AND %s ORDER BY utc_date''', (start,end)).fetchall()
        sites = c.execute('''SELECT s.id, s.company_id, s.name, s.airport_ident,
                s.is_base_maintenance, s.is_line_maintenance, co.name AS company_name,
                coalesce(s.latitude_deg,a.latitude_deg) AS latitude_deg,
                coalesce(s.longitude_deg,a.longitude_deg) AS longitude_deg,
                CASE WHEN s.latitude_deg IS NOT NULL THEN 'SITE_COORDINATE'
                     WHEN a.latitude_deg IS NOT NULL THEN 'AIRPORT_CENTROID' ELSE 'UNLOCATED' END AS location_precision
            FROM company_site s JOIN company co ON co.id=s.company_id
            LEFT JOIN airport a ON a.ident=s.airport_ident
            WHERE s.active AND co.active AND EXISTS (SELECT 1 FROM regulatory_approval r
                WHERE r.company_id=co.id AND regexp_replace(upper(r.approval_type),'[^A-Z0-9]','','g')
                    IN ('PART145','EASAPART145','UKPART145','EASA145','UK145'))
            ORDER BY co.name,s.name,s.id LIMIT 5001''').fetchall()
        companies = list({s['company_id'] for s in sites[:5000]})
        approvals = c.execute('''SELECT r.id,r.company_id,r.approval_number,r.status AS approval_status,
                r.authority_code,r.valid_from,r.valid_to,r.source_url,r.last_verified_at,
                ARRAY(SELECT sa.company_site_id FROM company_site_approval sa
                      WHERE sa.regulatory_approval_id=r.id AND sa.active) AS linked_site_ids
            FROM regulatory_approval r WHERE r.company_id=ANY(%s)
                AND regexp_replace(upper(r.approval_type),'[^A-Z0-9]','','g')
                    IN ('PART145','EASAPART145','UKPART145','EASA145','UK145')
            ORDER BY r.id''', (companies,)).fetchall()
        caps = c.execute('''SELECT ac.regulatory_approval_id, ac.company_site_id,
                ac.capability_kind,ac.rating_code,ac.manufacturer,ac.model,ac.aircraft_type_code,
                ac.limitation,ac.is_base_maintenance,ac.is_line_maintenance
            FROM approval_capability ac WHERE ac.active AND ac.regulatory_approval_id=ANY(%s)
            ORDER BY ac.id LIMIT 20001''', ([a['id'] for a in approvals],)).fetchall()
    by_company = {}
    by_approval = {}
    for approval in approvals:
        by_company.setdefault(approval['company_id'], []).append(approval)
    for cap in caps[:20000]:
        by_approval.setdefault(cap['regulatory_approval_id'], []).append(cap)
    today = date.today()
    for site in sites[:5000]:
        site['approvals'] = by_company.get(site['company_id'], [])
        site['capabilities'] = []
        for approval in site['approvals']:
            for cap in by_approval.get(approval['id'], []):
                if cap['company_site_id'] not in (None,site['id']):
                    continue
                # Company-wide scope is context, never promoted to site-specific approval.
                row = {**approval, **cap}
                row['match'] = capability_match(row, type_code, today)
                site['capabilities'].append(row)
        statuses = {cap['match'] for cap in site['capabilities']}
        site['match'] = next((s for s in ('SITE_MATCH','COMPANY_MATCH','APPROVAL_NOT_CURRENT')
                              if s in statuses), 'NO_RECORDED_MATCH')
    # Keep daily visit episodes separate: overnight silence is not proof of a stay.
    stops = sorted(visits[:500], key=lambda v: (v['first_evidence_at'], v['address'],
                                               v['dataset_day_id'], v['visit_sequence']))
    for number, stop in enumerate(stops, 1):
        stop['number'] = number
        stop['evidence_span_seconds'] = max(0, int(
            (stop['last_evidence_at'] - stop['first_evidence_at']).total_seconds()))
    return {'watch':watch,'from':start,'to':end,'latest_processed':latest,
            'type_code':type_code,'type_codes':codes,'aircraft_days':days,
            'addresses':sorted({d['address'] for d in days}),
            'processed_days':processed,'expected_days':(end-start).days+1,
            'stops':stops,'stops_truncated':len(visits)>500,
            'sites':sites[:5000],'sites_truncated':len(sites)>5000,
            'capabilities_truncated':len(caps)>20000,'approval_as_of':today}
