"""Read-only review of activity whose historical registration is missing."""
from datetime import date, timedelta
from psycopg.rows import dict_row


def unidentified_activity(store, start=None, end=None, offset=0):
    if offset < 0 or offset > 100000:
        raise ValueError('Invalid page offset')
    with store.connect() as c:
        c.row_factory = dict_row
        c.execute("SET LOCAL statement_timeout = '15s'")
        latest = c.execute("SELECT max(utc_date) AS day FROM dataset_day WHERE status='PROCESSED'").fetchone()['day']
        end = date.fromisoformat(end) if end else latest or date.today()
        start = date.fromisoformat(start) if start else end - timedelta(days=6)
        if end < start or (end-start).days > 30:
            raise ValueError('Choose an ordered date range of at most 31 days')
        coverage = c.execute("SELECT count(DISTINCT utc_date) AS days FROM dataset_day WHERE status='PROCESSED' AND utc_date BETWEEN %s AND %s", (start,end)).fetchone()['days']
        rows = c.execute('''WITH missing AS (
            SELECT d.* FROM aircraft_day d JOIN dataset_day q ON q.id=d.dataset_day_id
            WHERE q.status='PROCESSED' AND d.utc_date BETWEEN %s AND %s
              AND nullif(btrim(d.registration),'') IS NULL AND d.position_count > 0
        ), ranked AS (
            SELECT address,count(DISTINCT utc_date) AS days,min(utc_date) AS first_day,
                max(utc_date) AS last_day,sum(position_count) AS positions,
                sum(airborne_time_seconds)/3600.0 AS hours,
                array_remove(array_agg(DISTINCT nullif(btrim(type_code),'')),NULL) AS types
            FROM missing GROUP BY address
            ORDER BY hours DESC,address LIMIT 51 OFFSET %s
        ) SELECT r.*,a.registration AS current_registration,
            ARRAY(SELECT DISTINCT btrim(callsign) FROM missing m CROSS JOIN LATERAL unnest(m.callsigns) callsign
                WHERE m.address=r.address AND btrim(callsign)<>'' ORDER BY 1 LIMIT 20) AS callsigns
          FROM ranked r JOIN aircraft a USING(address) ORDER BY hours DESC,address''', (start,end,offset)).fetchall()
    return {'from':start,'to':end,'processed_days':coverage,'expected_days':(end-start).days+1,
            'rows':rows[:50],'has_more':len(rows)>50,'offset':offset}
