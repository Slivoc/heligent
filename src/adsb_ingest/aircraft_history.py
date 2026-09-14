"""Read-only tail history and cross-day quiet intervals from compact evidence."""
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from statistics import median

from psycopg.rows import dict_row

from .maintenance import registration


def history_window(start, end, latest, earliest=None):
    end = date.fromisoformat(end) if end else (latest or datetime.now(UTC).date())
    start = date.fromisoformat(start) if start else max(
        end - timedelta(days=89), min(earliest or end - timedelta(days=89), end))
    if end < start or (end - start).days > 365:
        raise ValueError('Choose an ordered UTC date range of at most 366 days')
    if end > datetime.now(UTC).date():
        raise ValueError('The end date cannot be in the future')
    return start, end


def _tail_key(value):
    return str(value or '').upper().replace('-', '').replace(' ', '')


def quiet_intervals(days, visits, tail, through):
    """Never bridge an observation, a changed registration, or an address change.

    All aircraft-days for matching addresses (including nonmatching identities)
    must be supplied. Visits must be complete; truncated lists are not suitable.
    """
    by_address, by_day = defaultdict(list), defaultdict(list)
    for d in days:
        by_address[d['address']].append(d)
    for v in visits:
        by_day[(v['dataset_day_id'], v['address'])].append(v)

    def boundary(d, side):
        if (_tail_key(d['registration']) != tail or not d['derivation_version']
                or d['dataset_status'] != 'PROCESSED'):
            return None
        field, seen, flag = (('first_evidence_at', 'first_seen_at', 'open_at_start')
                             if side == 'start' else
                             ('last_evidence_at', 'last_seen_at', 'open_at_end'))
        eligible = [v for v in by_day[(d['dataset_day_id'], d['address'])]
                    if v[flag] and abs((v[field] - d[seen]).total_seconds()) <= 60]
        # Ambiguous overlapping visits do not establish a single endpoint.
        return eligible[0] if len(eligible) == 1 else None

    result = []
    for address, observed in by_address.items():
        observed.sort(key=lambda d: (d['first_seen_at'], d['dataset_day_id']))
        for i, before in enumerate(observed):
            left = boundary(before, 'end')
            if not left:
                continue
            after = observed[i + 1] if i + 1 < len(observed) else None
            right = boundary(after, 'start') if after else None
            if after and (not right or left['airport_ident'] != right['airport_ident']):
                continue
            started = before['last_seen_at']
            ended = after['first_seen_at'] if after else through
            if not ended or ended <= started or (ended - started).total_seconds() < 6 * 3600:
                continue
            result.append({
                'key': f"{address}:{started.isoformat()}", 'address': address,
                'airport_ident': left['airport_ident'], 'airport_name': left['airport_name'],
                'started_at': started, 'ended_at': ended,
                'elapsed_seconds': int((ended - started).total_seconds()),
                'open_end': after is None, 'before': left, 'after': right,
                'ground_at_both_boundaries': bool(right and left['ground_observation_count']
                                                and right['ground_observation_count']),
            })
    return sorted(result, key=lambda g: g['started_at'])


def history_data(store, tail, start=None, end=None):
    tail = registration(tail)
    with store.connect() as c:
        c.row_factory = dict_row
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        c.execute("SET LOCAL statement_timeout = '20s'")
        bounds = c.execute('''SELECT min(utc_date) AS first, max(utc_date) AS last
            FROM dataset_day WHERE status='PROCESSED' AND derivation_version IS NOT NULL''').fetchone()
        start, end = history_window(start, end, bounds['last'], bounds['first'])
        coverage = c.execute('''SELECT utc_date, status::text AS status, derivation_version
            FROM dataset_day WHERE utc_date BETWEEN %s AND %s ORDER BY utc_date''', (start, end)).fetchall()
        addresses = [r['address'] for r in c.execute('''SELECT DISTINCT address FROM aircraft_day
            WHERE replace(upper(registration),'-','')=%s AND utc_date BETWEEN %s AND %s
            ORDER BY address LIMIT 65''', (tail, start, end)).fetchall()]
        if len(addresses) > 64:
            raise ValueError('Too many aircraft identities in this range; narrow the dates')
        # Include every observed day for those addresses. Filtering by registration
        # here could incorrectly join a quiet interval across an identity change.
        days = c.execute('''SELECT d.dataset_day_id,d.utc_date,d.address,d.registration,d.type_code,
                d.type_description,d.first_seen_at,d.last_seen_at,d.observation_count,
                d.ground_observation_count,d.airborne_time_seconds,d.position_source_types,
                q.derivation_version,q.status::text AS dataset_status
            FROM aircraft_day d JOIN dataset_day q ON q.id=d.dataset_day_id
            WHERE d.address=ANY(%s) AND d.utc_date BETWEEN %s AND %s
            ORDER BY d.first_seen_at,d.address LIMIT 5001''', (addresses, start, end)).fetchall()
        visits = c.execute('''SELECT v.*,a.name AS airport_name
            FROM aircraft_airport_visit v JOIN airport a ON a.ident=v.airport_ident
            WHERE v.address=ANY(%s) AND v.utc_date BETWEEN %s AND %s
            ORDER BY v.first_evidence_at,v.address,v.visit_sequence LIMIT 10001''',
                           (addresses, start, end)).fetchall()
        if len(days) > 5000 or len(visits) > 10000:
            raise ValueError('Too much history to infer complete intervals; narrow the dates')
        matching = [d for d in days if _tail_key(d['registration']) == tail]
        day_keys = {(d['dataset_day_id'], d['address']) for d in matching}
        flights = c.execute('''SELECT f.*,a.name AS origin_name,b.name AS destination_name
            FROM aircraft_flight_segment f JOIN aircraft_day d
              ON d.dataset_day_id=f.dataset_day_id AND d.address=f.address
            LEFT JOIN airport a ON a.ident=f.origin_airport_ident
            LEFT JOIN airport b ON b.ident=f.destination_airport_ident
            WHERE replace(upper(d.registration),'-','')=%s AND f.utc_date BETWEEN %s AND %s
            ORDER BY f.takeoff_at DESC,f.address,f.segment_sequence LIMIT 5001''',
                            (tail, start, end)).fetchall()
        flights_truncated = len(flights) > 5000
        flights = flights[:5000]
        through = datetime.combine(min(end, bounds['last']) + timedelta(days=1), time(), UTC) if bounds['last'] else None
        intervals = quiet_intervals(days, visits, tail, through)
        airports = sorted({g['airport_ident'] for g in intervals})
        peers = c.execute('''SELECT v.airport_ident,v.utc_date,count(DISTINCT v.address) AS other_aircraft,
                count(DISTINCT v.address) FILTER (WHERE cl.category='ROTORCRAFT') AS other_helicopters
            FROM aircraft_airport_visit v JOIN dataset_day q ON q.id=v.dataset_day_id
            JOIN aircraft_day d ON d.dataset_day_id=v.dataset_day_id AND d.address=v.address
            LEFT JOIN aircraft_type_classification cl ON cl.type_code=d.type_code
            WHERE v.airport_ident=ANY(%s) AND v.utc_date BETWEEN %s AND %s
              AND NOT (v.address=ANY(%s)) AND q.status='PROCESSED'
            GROUP BY v.airport_ident,v.utc_date''', (airports, start, end, addresses)).fetchall() if airports else []
        mros = c.execute('''SELECT s.airport_ident,array_agg(DISTINCT co.name ORDER BY co.name) AS companies
            FROM company_site s JOIN company co ON co.id=s.company_id
            WHERE s.airport_ident=ANY(%s) AND s.active AND co.active AND co.is_mro
            GROUP BY s.airport_ident''', (airports,)).fetchall() if airports else []
        events = c.execute('''SELECT e.id,e.started_at,e.ended_at,e.maintenance_kind,e.status,e.notes,e.reviewed_by
            FROM maintenance_event e JOIN maintenance_watch w ON w.id=e.watch_id
            JOIN maintenance_watchlist l ON l.id=w.watchlist_id
            WHERE l.scope_key='internal' AND w.registration=%s
              AND e.ended_at >= %s AND e.started_at < %s::date + 1
            ORDER BY e.started_at DESC LIMIT 501''', (tail, start, end)).fetchall()

    complete_dates = {d['utc_date'] for d in coverage if d['status'] == 'PROCESSED' and d['derivation_version']}
    peer_lookup = {(p['airport_ident'], p['utc_date']): p for p in peers}
    mro_lookup = {m['airport_ident']: m['companies'] for m in mros}
    for gap in intervals:
        # For an open interval, midnight marks the end of the previous source day.
        last_date = (gap['ended_at'] - timedelta(microseconds=1)).date() if gap['open_end'] else gap['ended_at'].date()
        first_date = gap['started_at'].date()
        span_dates = [first_date + timedelta(days=i) for i in range((last_date-first_date).days+1)]
        inside_dates = [d for d in span_dates if d > first_date and (gap['open_end'] or d < last_date)]
        peer_rows = [peer_lookup.get((gap['airport_ident'], d), {}) for d in inside_dates]
        gap.update({
            'processed_days': sum(d in complete_dates for d in span_dates), 'expected_days': len(span_dates),
            'intermediate_days': len(inside_dates),
            'days_with_other_aircraft': sum(p.get('other_aircraft', 0) > 0 for p in peer_rows),
            'days_with_other_helicopters': sum(p.get('other_helicopters', 0) > 0 for p in peer_rows),
            'min_other_aircraft': min((p.get('other_aircraft', 0) for p in peer_rows), default=0),
            'mro_companies': mro_lookup.get(gap['airport_ident'], []),
            'previous_flight': next((f for f in flights if f['address'] == gap['address']
                                    and f['landing_at'] <= gap['started_at']), None),
            'next_flight': next((f for f in reversed(flights) if f['address'] == gap['address']
                                and f['takeoff_at'] >= gap['ended_at']), None) if not gap['open_end'] else None,
        })
    closed = [g for g in intervals if not g['open_end']]
    bases = Counter(g['airport_ident'] for g in closed)
    codes = sorted({d['type_code'] for d in matching if d['type_code']})
    matching_visits = [v for v in visits if (v['dataset_day_id'], v['address']) in day_keys]
    observed_dates = {d['utc_date'] for d in matching}
    return {
        'registration': tail, 'from': start, 'to': end, 'latest_processed': bounds['last'],
        'type_codes': codes, 'addresses': addresses,
        'coverage': {'processed_days': len(complete_dates), 'expected_days': (end-start).days+1},
        'calendar': [{'utc_date': start+timedelta(days=i),
                      'processed': start+timedelta(days=i) in complete_dates,
                      'observed': start+timedelta(days=i) in observed_dates}
                     for i in range((end-start).days+1)],
        'summary': {'observed_days': len(observed_dates), 'quiet_windows': len(closed),
                    'median_quiet_seconds': median(g['elapsed_seconds'] for g in closed) if closed else None,
                    'observations': sum(d['observation_count'] for d in matching),
                    'ground_observations': sum(d['ground_observation_count'] for d in matching),
                    'observed_ground_seconds': sum(v['ground_time_seconds'] for v in matching_visits)},
        'bases': [{'airport_ident': ident, 'airport_name': next(g['airport_name'] for g in closed if g['airport_ident'] == ident),
                   'windows': n} for ident, n in bases.most_common(10)],
        'days': matching, 'intervals': intervals, 'flights': flights, 'flights_truncated': flights_truncated,
        'events': events[:500], 'events_truncated': len(events) > 500,
    }
