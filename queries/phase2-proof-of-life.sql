-- Run with:
-- psql "$DATABASE_URL" -f queries/phase2-proof-of-life.sql

\set report_date '2026-08-20'

SELECT
    utc_date,
    status,
    source_release_tag,
    source_aircraft_count,
    source_record_count,
    airport_presence_record_count,
    processing_duration_ms,
    raw_deleted_at
FROM dataset_day
WHERE utc_date = :'report_date'::date;

-- Busiest hubs by unique observed tails.
SELECT
    airport_ident,
    airport_name,
    airport_type,
    iata_code,
    unique_aircraft AS unique_tails,
    primary_aircraft,
    round(ground_active_hours::numeric, 1) AS ground_active_hours,
    round(primary_tail_active_hours::numeric, 1) AS primary_tail_active_hours
FROM airport_day_metrics
WHERE utc_date = :'report_date'::date
ORDER BY unique_tails DESC
LIMIT 30;

-- Aircraft-type activity across the complete day.
SELECT
    type_code,
    unique_aircraft,
    observations,
    round(active_hours::numeric, 1) AS active_hours,
    round(airborne_hours::numeric, 1) AS airborne_hours,
    airport_links
FROM aircraft_type_day_metrics
WHERE utc_date = :'report_date'::date
ORDER BY unique_aircraft DESC
LIMIT 30;

-- Most active identifiable tails and their primary observed airport.
SELECT
    ad.registration,
    ad.address,
    ad.type_code,
    round((ad.active_time_seconds / 3600.0)::numeric, 2) AS active_hours,
    round((ad.airborne_time_seconds / 3600.0)::numeric, 2) AS airborne_hours,
    aad.airport_ident AS primary_airport,
    (aad.airport_ident IS NOT NULL) AS has_primary_airport
FROM aircraft_day ad
LEFT JOIN aircraft_airport_day aad
  ON aad.dataset_day_id = ad.dataset_day_id
 AND aad.address = ad.address
 AND aad.is_primary_airport
WHERE ad.utc_date = :'report_date'::date
  AND ad.registration IS NOT NULL
ORDER BY ad.active_time_seconds DESC
LIMIT 50;

-- Type mix at each hub. Add an airport_ident predicate for a selected hub.
SELECT
    airport_ident,
    type_code,
    unique_aircraft,
    primary_aircraft,
    tail_airport_links,
    round(observed_ground_hours::numeric, 1) AS observed_ground_hours,
    round(primary_tail_airborne_hours::numeric, 1) AS primary_tail_airborne_hours
FROM airport_day_type_metrics
WHERE utc_date = :'report_date'::date
ORDER BY unique_aircraft DESC
LIMIT 100;
