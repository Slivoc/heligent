"""Shared team watchlist and durable analyst evidence, independent of raw rebuilds."""
from datetime import datetime, timezone
import re

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def registration(value):
    result = str(value or '').strip().upper().replace('-', '').replace(' ', '')
    if not re.fullmatch(r'[A-Z0-9]{3,12}', result):
        raise ValueError('Enter a valid aircraft registration')
    return result


def event_input(payload):
    if not isinstance(payload, dict):
        raise ValueError('Review must be a JSON object')
    start = datetime.fromisoformat(str(payload.get('started_at', '')).replace('Z', '+00:00'))
    end = datetime.fromisoformat(str(payload.get('ended_at', '')).replace('Z', '+00:00'))
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError('Maintenance timestamps must include a timezone')
    if end < start or end > datetime.now(timezone.utc):
        raise ValueError('Maintenance dates must be ordered and in the past')
    status = payload.get('status')
    if status not in {'CONFIRMED', 'REJECTED', 'UNCERTAIN'}:
        raise ValueError('Invalid review status')
    notes = str(payload.get('notes', '')).strip()
    kind = str(payload.get('maintenance_kind', '')).strip()
    if not notes or len(notes) > 4000 or not kind or len(kind) > 100:
        raise ValueError('Provide a maintenance type and evidence notes (up to 4000 characters)')
    return start, end, kind, status, notes


class MaintenanceStore:
    def __init__(self, store):
        self.store = store

    def watches(self):
        with self.store.connect() as c:
            c.row_factory = dict_row
            c.execute("SET LOCAL statement_timeout = '15s'")
            return c.execute('''SELECT w.*, aircraft.type_code, aircraft.utc_date AS last_seen_date,
                operator.operator, operator.operator_source_code,
                (SELECT max(ended_at) FROM maintenance_event e
                WHERE e.watch_id=w.id AND e.status='CONFIRMED') AS last_confirmed_maintenance
                FROM maintenance_watch w JOIN maintenance_watchlist l ON l.id=w.watchlist_id
                LEFT JOIN LATERAL (
                    SELECT ad.address, ad.type_code, ad.utc_date
                    FROM aircraft_day ad JOIN dataset_day d ON d.id=ad.dataset_day_id
                    WHERE replace(upper(ad.registration),'-','')=w.registration
                      AND d.status='PROCESSED'
                    ORDER BY ad.utc_date DESC, ad.last_seen_at DESC, ad.address
                    LIMIT 1
                ) aircraft ON true
                LEFT JOIN LATERAL (
                    SELECT claim.operator, claim.operator_source_code
                    FROM current_aircraft_operator_claim claim
                    WHERE claim.aircraft_address=aircraft.address
                       OR claim.reported_aircraft_address=aircraft.address
                       OR claim.registration_key=w.registration
                    ORDER BY CASE WHEN claim.aircraft_address=aircraft.address THEN 0
                                  WHEN claim.reported_aircraft_address=aircraft.address THEN 1
                                  ELSE 2 END,
                        claim.confidence DESC NULLS LAST,
                        claim.operator_source_code, claim.operator
                    LIMIT 1
                ) operator ON true
                WHERE l.scope_key='internal' AND w.active ORDER BY w.registration''').fetchall()

    def add(self, payload, actor):
        if not isinstance(payload, dict):
            raise ValueError('Watch must be a JSON object')
        tail = registration(payload.get('registration'))
        notes = str(payload.get('notes', '')).strip()
        if len(notes) > 4000:
            raise ValueError('Notes must be at most 4000 characters')
        with self.store.connect() as c:
            c.row_factory = dict_row
            return c.execute('''INSERT INTO maintenance_watch(watchlist_id,registration,notes,created_by)
                SELECT id,%s,%s,%s FROM maintenance_watchlist WHERE scope_key='internal'
                ON CONFLICT(watchlist_id,registration) DO UPDATE SET active=true,notes=excluded.notes
                RETURNING *''', (tail, notes, actor)).fetchone()

    def detail(self, watch_id):
        with self.store.connect() as c:
            c.row_factory = dict_row
            c.execute("SET LOCAL statement_timeout = '15s'")
            watch = c.execute('''SELECT w.* FROM maintenance_watch w JOIN maintenance_watchlist l
                ON l.id=w.watchlist_id WHERE w.id=%s AND l.scope_key='internal' ''', (watch_id,)).fetchone()
            if watch is None:
                raise ValueError('Watch not found')
            tail = watch['registration']
            # Bound review queries to the latest 90 processed dates. Full historical events persist separately.
            latest = c.execute("SELECT max(utc_date) FROM dataset_day WHERE status='PROCESSED' AND derivation_version IS NOT NULL").fetchone()['max']
            data = {'watch': watch, 'as_of': latest}
            for key, view, order in [('flights','nl_aircraft_flight','takeoff_at'), ('visits','nl_airport_visit','first_evidence_at'), ('candidates','nl_mro_stay_candidate','candidate_started_at')]:
                time_col = 'candidate_started_at' if key == 'candidates' else 'utc_date'
                rows = c.execute(f'''SELECT * FROM {view}
                    WHERE replace(upper(registration),'-','')=%s
                    AND {time_col} >= %s::date - 89 ORDER BY {order} DESC LIMIT 501''', (tail,latest)).fetchall()
                data[key] = rows[:500]
                data[key + '_truncated'] = len(rows) > 500
            data['events'] = c.execute('SELECT * FROM maintenance_event WHERE watch_id=%s ORDER BY ended_at DESC', (watch_id,)).fetchall()
            confirmed = [e for e in data['events'] if e['status'] == 'CONFIRMED']
            data['since_maintenance'] = None
            if confirmed and latest and confirmed[0]['ended_at'].date() <= latest:
                baseline = confirmed[0]['ended_at']
                data['since_maintenance'] = c.execute('''SELECT count(*) AS episodes,
                    COALESCE(sum(observed_airborne_hours),0) AS observed_hours,
                    COALESCE(sum(elapsed_airborne_hours),0) AS elapsed_hours
                    FROM nl_aircraft_flight WHERE replace(upper(registration),'-','')=%s
                    AND takeoff_at >= %s''', (tail,baseline)).fetchone()
                coverage = c.execute('''SELECT count(DISTINCT utc_date) AS days FROM dataset_day
                    WHERE status='PROCESSED' AND derivation_version IS NOT NULL
                    AND utc_date BETWEEN %s AND %s''', (baseline.date(),latest)).fetchone()['days']
                data['since_maintenance'].update({'baseline': baseline, 'processed_days': coverage,
                    'expected_days': (latest-baseline.date()).days+1})
            return data

    def archive(self, watch_id):
        with self.store.connect() as c:
            c.execute('''UPDATE maintenance_watch SET active=false WHERE id=%s
                AND watchlist_id=(SELECT id FROM maintenance_watchlist WHERE scope_key='internal')''', (watch_id,))

    def review(self, watch_id, payload, actor):
        start, end, kind, status, notes = event_input(payload)
        candidate_key = payload.get('candidate_key') or None
        # Snapshot inferred evidence so a subsequent parser rebuild never silently rewrites a review.
        detail = self.detail(watch_id)
        evidence = {}
        if candidate_key:
            candidate = next((r for r in detail['candidates'] if r['candidate_key'] == candidate_key), None)
            if candidate is None:
                raise ValueError('Candidate is no longer in the review window; refresh the timeline')
            evidence = {k: str(v) for k,v in candidate.items()}
            start, end = candidate['candidate_started_at'], candidate['candidate_ended_at']
        with self.store.connect() as c:
            c.row_factory = dict_row
            event_id = payload.get('event_id')
            if event_id:
                old = c.execute('SELECT * FROM maintenance_event WHERE id=%s AND watch_id=%s FOR UPDATE', (int(event_id),watch_id)).fetchone()
                if old is None:
                    raise ValueError('Review not found')
                c.execute('INSERT INTO maintenance_event_revision(event_id,snapshot) SELECT id,to_jsonb(e) FROM maintenance_event e WHERE id=%s', (old['id'],))
                return c.execute('''UPDATE maintenance_event SET status=%s, notes=%s,
                    maintenance_kind=%s, reviewed_by=%s, reviewed_at=now() WHERE id=%s RETURNING *''',
                    (status,notes,kind,actor,old['id'])).fetchone()
            if candidate_key:
                c.execute('''INSERT INTO maintenance_event_revision(event_id,snapshot)
                    SELECT id,to_jsonb(e) FROM maintenance_event e WHERE watch_id=%s AND candidate_key=%s''', (watch_id,candidate_key))
            return c.execute('''INSERT INTO maintenance_event
                (watch_id,candidate_key,started_at,ended_at,maintenance_kind,status,notes,evidence,reviewed_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(watch_id,candidate_key) DO UPDATE SET
                status=excluded.status, notes=excluded.notes, maintenance_kind=excluded.maintenance_kind,
                reviewed_by=excluded.reviewed_by, reviewed_at=now(), evidence=excluded.evidence
                RETURNING *''', (watch_id,candidate_key,start,end,kind,status,notes,Jsonb(evidence),actor)).fetchone()
