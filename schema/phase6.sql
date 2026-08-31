BEGIN;

ALTER TABLE aircraft_airport_day
    ADD COLUMN IF NOT EXISTS inferred_endpoint_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS arrival_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS departure_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS link_method text NOT NULL DEFAULT 'GROUND';

ALTER TABLE aircraft_airport_day
    DROP CONSTRAINT IF EXISTS aircraft_airport_day_nonnegative_metrics,
    DROP CONSTRAINT IF EXISTS aircraft_airport_day_link_method_check;

ALTER TABLE aircraft_airport_day
    ADD CONSTRAINT aircraft_airport_day_nonnegative_metrics CHECK (
        presence_count = 1
        AND ground_observation_count >= 0
        AND ground_time_seconds >= 0
        AND ground_active_time_seconds >= 0
        AND closest_distance_nm >= 0
        AND inferred_endpoint_count >= 0
        AND arrival_count >= 0
        AND departure_count >= 0
        AND ground_observation_count + inferred_endpoint_count > 0
    ),
    ADD CONSTRAINT aircraft_airport_day_link_method_check CHECK (
        link_method IN ('GROUND', 'INFERRED_ENDPOINT', 'GROUND_AND_ENDPOINT')
    );

CREATE OR REPLACE VIEW airport_day_metrics AS
SELECT
    aad.utc_date,
    aad.airport_ident,
    a.name AS airport_name,
    a.airport_type,
    a.iata_code,
    count(*) AS unique_aircraft,
    count(*) FILTER (WHERE aad.is_primary_airport) AS primary_aircraft,
    count(*) FILTER (WHERE ad.registration IS NOT NULL) AS registered_aircraft,
    count(DISTINCT ad.type_code) FILTER (WHERE ad.type_code IS NOT NULL)
        AS known_type_codes,
    sum(aad.ground_observation_count) AS ground_observations,
    sum(aad.ground_time_seconds) / 3600.0 AS observed_ground_hours,
    sum(aad.ground_active_time_seconds) / 3600.0 AS ground_active_hours,
    sum(ad.active_time_seconds) FILTER (WHERE aad.is_primary_airport) / 3600.0
        AS primary_tail_active_hours,
    sum(ad.airborne_time_seconds) FILTER (WHERE aad.is_primary_airport) / 3600.0
        AS primary_tail_airborne_hours,
    sum(aad.arrival_count) AS arrival_candidates,
    sum(aad.departure_count) AS departure_candidates,
    sum(aad.arrival_count + aad.departure_count) AS movement_candidates,
    count(*) FILTER (WHERE aad.inferred_endpoint_count > 0) AS endpoint_linked_aircraft
FROM aircraft_airport_day aad
JOIN aircraft_day ad
  ON ad.dataset_day_id = aad.dataset_day_id
 AND ad.address = aad.address
JOIN airport a ON a.ident = aad.airport_ident
GROUP BY aad.utc_date, aad.airport_ident, a.name, a.airport_type, a.iata_code;

CREATE OR REPLACE VIEW airport_day_type_metrics AS
SELECT
    aad.utc_date,
    aad.airport_ident,
    COALESCE(ad.type_code, 'UNKNOWN') AS type_code,
    count(*) AS unique_aircraft,
    count(*) FILTER (WHERE aad.is_primary_airport) AS primary_aircraft,
    count(*) AS tail_airport_links,
    sum(aad.ground_time_seconds) / 3600.0 AS observed_ground_hours,
    sum(aad.ground_active_time_seconds) / 3600.0 AS ground_active_hours,
    sum(ad.active_time_seconds) FILTER (WHERE aad.is_primary_airport) / 3600.0
        AS primary_tail_active_hours,
    sum(ad.airborne_time_seconds) FILTER (WHERE aad.is_primary_airport) / 3600.0
        AS primary_tail_airborne_hours,
    sum(aad.arrival_count) AS arrival_candidates,
    sum(aad.departure_count) AS departure_candidates,
    sum(aad.arrival_count + aad.departure_count) AS movement_candidates
FROM aircraft_airport_day aad
JOIN aircraft_day ad
  ON ad.dataset_day_id = aad.dataset_day_id
 AND ad.address = aad.address
GROUP BY aad.utc_date, aad.airport_ident, COALESCE(ad.type_code, 'UNKNOWN');

COMMIT;
