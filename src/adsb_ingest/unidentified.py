"""Read-only review of activity whose historical registration is missing."""
from datetime import date, timedelta
import re
from psycopg.rows import dict_row


def unidentified_activity(store, start=None, end=None, offset=0, category='ROTORCRAFT_UNKNOWN', region='ALL', search='', *, gap='SOURCE_TAIL'):
    predicates = {
        'SOURCE_TAIL': "nullif(btrim(d.source_registration),'') IS NULL",
        'TAIL': "nullif(btrim(d.registration),'') IS NULL",
        'TYPE': "nullif(btrim(d.type_code),'') IS NULL",
        'BOTH': "nullif(btrim(d.registration),'') IS NULL AND nullif(btrim(d.type_code),'') IS NULL",
    }
    if gap not in predicates:
        raise ValueError('Invalid identity gap')
    predicate = predicates[gap]
    if category not in ('ALL','ROTORCRAFT','ROTORCRAFT_UNKNOWN','UNKNOWN','FIXED_WING') or region not in ('ALL','EU','GB','NA','SA','AF','AS','OC','AN','UNLOCATED'):
        raise ValueError('Invalid aircraft category or region')
    if offset < 0 or offset > 100000:
        raise ValueError('Invalid page offset')
    if len(search) > 40:
        raise ValueError('Use at most 40 characters for a hex or callsign search')
    search_key = re.sub(r'[^A-Z0-9]', '', search.upper())
    if search.strip() and not search_key:
        raise ValueError('Enter a hex address or callsign containing letters or numbers')
    search_pattern = f'%{search_key}%'
    with store.connect() as c:
        c.row_factory = dict_row
        c.execute("SET LOCAL statement_timeout = '15s'")
        latest = c.execute("SELECT max(utc_date) AS day FROM dataset_day WHERE status='PROCESSED'").fetchone()['day']
        end = date.fromisoformat(end) if end else latest or date.today()
        start = date.fromisoformat(start) if start else end - timedelta(days=6)
        if end < start or (end-start).days > 30:
            raise ValueError('Choose an ordered date range of at most 31 days')
        coverage = c.execute("SELECT count(DISTINCT utc_date) AS days FROM dataset_day WHERE status='PROCESSED' AND utc_date BETWEEN %s AND %s", (start,end)).fetchone()['days']
        rows = c.execute(f'''WITH missing AS (
            SELECT d.* FROM aircraft_day_identity d JOIN dataset_day q ON q.id=d.dataset_day_id
            WHERE q.status='PROCESSED' AND d.utc_date BETWEEN %s AND %s
              AND {predicate} AND d.position_count > 0
              AND (%s='ALL' OR d.resolved_category::text=%s
                   OR (%s='ROTORCRAFT_UNKNOWN' AND d.resolved_category::text IN ('ROTORCRAFT','UNKNOWN')))
              AND (%s='ALL' OR EXISTS (SELECT 1 FROM aircraft_airport_visit v JOIN airport ap ON ap.ident=v.airport_ident
                  WHERE v.dataset_day_id=d.dataset_day_id AND v.address=d.address
                  AND (ap.continent=%s OR (%s='GB' AND ap.iso_country='GB')))
                  OR (%s='UNLOCATED' AND NOT EXISTS (SELECT 1 FROM aircraft_airport_visit v JOIN airport ap ON ap.ident=v.airport_ident
                      WHERE v.dataset_day_id=d.dataset_day_id AND v.address=d.address AND ap.continent IS NOT NULL)))
        ), ranked AS (
            SELECT address,count(DISTINCT utc_date) AS days,min(utc_date) AS first_day,
                max(utc_date) AS last_day,sum(position_count) AS positions,
                sum(airborne_time_seconds)/3600.0 AS hours,
                array_remove(array_agg(DISTINCT nullif(btrim(source_type_code),'')),NULL) AS types,
                array_agg(DISTINCT resolved_category::text) AS categories,
                array_remove(array_agg(DISTINCT identity_source_code),NULL) AS identity_sources,
                array_remove(array_agg(DISTINCT nullif(btrim(registration),'')),NULL) AS resolved_registrations,
                bool_or(resolved_category='ROTORCRAFT') AS has_rotorcraft
            FROM missing GROUP BY address
            HAVING (%s='' OR upper(address) LIKE %s OR bool_or(EXISTS (
                SELECT 1 FROM unnest(callsigns) clue
                WHERE regexp_replace(upper(clue),'[^A-Z0-9]','','g') LIKE %s)))
            ORDER BY CASE WHEN %s='ROTORCRAFT_UNKNOWN' THEN bool_or(resolved_category='ROTORCRAFT') ELSE false END DESC,hours DESC,address LIMIT 51 OFFSET %s
        ) SELECT r.*,a.registration AS current_registration,
            ARRAY(SELECT DISTINCT btrim(callsign) FROM missing m CROSS JOIN LATERAL unnest(m.callsigns) callsign
                WHERE m.address=r.address AND btrim(callsign)<>'' ORDER BY 1 LIMIT 20) AS callsigns
          FROM ranked r JOIN aircraft a USING(address) ORDER BY CASE WHEN %s='ROTORCRAFT_UNKNOWN' THEN has_rotorcraft ELSE false END DESC,hours DESC,address''', (start,end,category,category,category,region,region,region,region,search_key,search_pattern,search_pattern,category,offset,category)).fetchall()
        visits = c.execute(f'''WITH grouped AS (SELECT v.address,v.airport_ident,ap.name,count(*) AS visits,
                sum(v.ground_time_seconds)/3600.0 AS ground_hours
                FROM aircraft_airport_visit v JOIN aircraft_day_identity d USING(dataset_day_id,address)
                JOIN dataset_day q ON q.id=d.dataset_day_id JOIN airport ap ON ap.ident=v.airport_ident
                WHERE v.address=ANY(%s) AND d.utc_date BETWEEN %s AND %s AND q.status='PROCESSED'
                AND {predicate} AND d.position_count>0
                AND (%s='ALL' OR d.resolved_category::text=%s
                   OR (%s='ROTORCRAFT_UNKNOWN' AND d.resolved_category::text IN ('ROTORCRAFT','UNKNOWN')))
                AND (%s='ALL' OR ap.continent=%s OR (%s='GB' AND ap.iso_country='GB')
                     OR (%s='UNLOCATED' AND ap.continent IS NULL))
                GROUP BY v.address,v.airport_ident,ap.name), ranked AS (
                  SELECT *,row_number() OVER(PARTITION BY address ORDER BY visits DESC,airport_ident) AS rank FROM grouped)
                SELECT * FROM ranked WHERE rank<=3 ORDER BY address,rank''',
                ([r['address'] for r in rows[:50]],start,end,category,category,category,region,region,region,region)).fetchall()
        for row in rows[:50]:
            row['top_visits'] = [v for v in visits if v['address']==row['address']]
    return {'from':start,'to':end,'processed_days':coverage,'expected_days':(end-start).days+1,
            'rows':rows[:50],'has_more':len(rows)>50,'offset':offset,'gap':gap}
