-- Phase 15: durable flight segments, repeated airport visits, and provisional
-- MRO-stay evidence derived from the raw per-aircraft traces.

BEGIN;

ALTER TABLE dataset_day
    ADD COLUMN IF NOT EXISTS flight_segment_record_count integer,
    ADD COLUMN IF NOT EXISTS airport_visit_record_count integer,
    ADD COLUMN IF NOT EXISTS derivation_version text,
    ADD COLUMN IF NOT EXISTS derivation_config jsonb;

ALTER TABLE dataset_day
    DROP CONSTRAINT IF EXISTS dataset_day_derivation_config_object;
ALTER TABLE dataset_day
    ADD CONSTRAINT dataset_day_derivation_config_object CHECK (
        derivation_config IS NULL OR jsonb_typeof(derivation_config) = 'object'
    );
ALTER TABLE dataset_day
    DROP CONSTRAINT IF EXISTS dataset_day_episode_counts_nonnegative;
ALTER TABLE dataset_day
    ADD CONSTRAINT dataset_day_episode_counts_nonnegative CHECK (
        (flight_segment_record_count IS NULL OR flight_segment_record_count >= 0)
        AND (airport_visit_record_count IS NULL OR airport_visit_record_count >= 0)
    );

ALTER TABLE aircraft_airport_day
    DROP CONSTRAINT IF EXISTS aircraft_airport_day_nonnegative_metrics;
ALTER TABLE aircraft_airport_day
    ADD CONSTRAINT aircraft_airport_day_nonnegative_metrics CHECK (
        presence_count >= 1
        AND ground_observation_count >= 0
        AND ground_time_seconds >= 0
        AND ground_active_time_seconds >= 0
        AND closest_distance_nm >= 0
        AND inferred_endpoint_count >= 0
        AND arrival_count >= 0
        AND departure_count >= 0
        AND ground_observation_count + inferred_endpoint_count > 0
    );

CREATE TABLE IF NOT EXISTS aircraft_flight_segment (
    dataset_day_id bigint NOT NULL,
    utc_date date NOT NULL,
    address varchar(7) NOT NULL,
    segment_sequence integer NOT NULL,
    first_airborne_at timestamptz NOT NULL,
    last_airborne_at timestamptz NOT NULL,
    takeoff_at timestamptz NOT NULL,
    landing_at timestamptz NOT NULL,
    origin_airport_ident text REFERENCES airport (ident),
    destination_airport_ident text REFERENCES airport (ident),
    origin_evidence text,
    destination_evidence text,
    observation_count integer NOT NULL,
    observed_airborne_seconds integer NOT NULL,
    elapsed_airborne_seconds integer NOT NULL,
    unobserved_seconds integer NOT NULL,
    estimated_distance_nm double precision,
    max_altitude_ft integer,
    max_ground_speed_knots real,
    callsigns text[] NOT NULL DEFAULT ARRAY[]::text[],
    starts_before_window boolean NOT NULL DEFAULT false,
    ends_after_window boolean NOT NULL DEFAULT false,
    confidence text NOT NULL,
    quality_flags text[] NOT NULL DEFAULT ARRAY[]::text[],
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (dataset_day_id, address, segment_sequence),
    CONSTRAINT aircraft_flight_segment_aircraft_day_fk
        FOREIGN KEY (dataset_day_id, address)
        REFERENCES aircraft_day (dataset_day_id, address)
        ON DELETE CASCADE,
    CONSTRAINT aircraft_flight_segment_dataset_date_fk
        FOREIGN KEY (dataset_day_id, utc_date)
        REFERENCES dataset_day (id, utc_date)
        ON DELETE CASCADE,
    CONSTRAINT aircraft_flight_segment_sequence_positive
        CHECK (segment_sequence > 0),
    CONSTRAINT aircraft_flight_segment_time_order CHECK (
        takeoff_at <= first_airborne_at
        AND first_airborne_at <= last_airborne_at
        AND last_airborne_at <= landing_at
    ),
    CONSTRAINT aircraft_flight_segment_nonnegative CHECK (
        observation_count > 0
        AND observed_airborne_seconds >= 0
        AND elapsed_airborne_seconds >= 0
        AND unobserved_seconds >= 0
        AND unobserved_seconds <= elapsed_airborne_seconds
        AND (estimated_distance_nm IS NULL OR estimated_distance_nm >= 0)
    ),
    CONSTRAINT aircraft_flight_segment_confidence_check
        CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH')),
    CONSTRAINT aircraft_flight_segment_origin_evidence_check CHECK (
        origin_evidence IS NULL
        OR origin_evidence IN ('GROUND', 'LOW_SLOW_PROXIMITY', 'INFERRED_ENDPOINT')
    ),
    CONSTRAINT aircraft_flight_segment_destination_evidence_check CHECK (
        destination_evidence IS NULL
        OR destination_evidence IN ('GROUND', 'LOW_SLOW_PROXIMITY', 'INFERRED_ENDPOINT')
    )
);

CREATE INDEX IF NOT EXISTS aircraft_flight_segment_tail_time_idx
    ON aircraft_flight_segment (address, takeoff_at DESC);
CREATE INDEX IF NOT EXISTS aircraft_flight_segment_origin_time_idx
    ON aircraft_flight_segment (origin_airport_ident, takeoff_at DESC)
    WHERE origin_airport_ident IS NOT NULL;
CREATE INDEX IF NOT EXISTS aircraft_flight_segment_destination_time_idx
    ON aircraft_flight_segment (destination_airport_ident, landing_at DESC)
    WHERE destination_airport_ident IS NOT NULL;

CREATE TABLE IF NOT EXISTS aircraft_airport_visit (
    dataset_day_id bigint NOT NULL,
    utc_date date NOT NULL,
    address varchar(7) NOT NULL,
    visit_sequence integer NOT NULL,
    airport_ident text NOT NULL REFERENCES airport (ident),
    first_evidence_at timestamptz NOT NULL,
    last_evidence_at timestamptz NOT NULL,
    arrived_at timestamptz,
    departed_at timestamptz,
    ground_observation_count integer NOT NULL,
    proximity_observation_count integer NOT NULL,
    ground_time_seconds integer NOT NULL,
    ground_active_time_seconds integer NOT NULL,
    closest_distance_nm real NOT NULL,
    arrival_evidence text,
    departure_evidence text,
    open_at_start boolean NOT NULL,
    open_at_end boolean NOT NULL,
    confidence text NOT NULL,
    quality_flags text[] NOT NULL DEFAULT ARRAY[]::text[],
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (dataset_day_id, address, visit_sequence),
    CONSTRAINT aircraft_airport_visit_aircraft_day_fk
        FOREIGN KEY (dataset_day_id, address)
        REFERENCES aircraft_day (dataset_day_id, address)
        ON DELETE CASCADE,
    CONSTRAINT aircraft_airport_visit_dataset_date_fk
        FOREIGN KEY (dataset_day_id, utc_date)
        REFERENCES dataset_day (id, utc_date)
        ON DELETE CASCADE,
    CONSTRAINT aircraft_airport_visit_sequence_positive CHECK (visit_sequence > 0),
    CONSTRAINT aircraft_airport_visit_time_order CHECK (
        (arrived_at IS NULL OR arrived_at <= first_evidence_at)
        AND first_evidence_at <= last_evidence_at
        AND (departed_at IS NULL OR last_evidence_at <= departed_at)
        AND (arrived_at IS NULL OR departed_at IS NULL OR arrived_at <= departed_at)
    ),
    CONSTRAINT aircraft_airport_visit_nonnegative CHECK (
        ground_observation_count >= 0
        AND proximity_observation_count > 0
        AND ground_observation_count <= proximity_observation_count
        AND ground_time_seconds >= 0
        AND ground_active_time_seconds >= 0
        AND closest_distance_nm >= 0
    ),
    CONSTRAINT aircraft_airport_visit_confidence_check
        CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH')),
    CONSTRAINT aircraft_airport_visit_open_boundary_check CHECK (
        open_at_start = (arrived_at IS NULL)
        AND open_at_end = (departed_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS aircraft_airport_visit_tail_time_idx
    ON aircraft_airport_visit (address, first_evidence_at DESC);
CREATE INDEX IF NOT EXISTS aircraft_airport_visit_airport_time_idx
    ON aircraft_airport_visit (airport_ident, first_evidence_at DESC, address);

CREATE OR REPLACE VIEW nl_aircraft_flight AS
SELECT
    segment.utc_date,
    segment.address,
    activity.registration,
    activity.type_code,
    activity.type_description,
    activity.category,
    activity.operator,
    activity.operator_country,
    activity.operator_home_region,
    segment.segment_sequence,
    segment.takeoff_at,
    segment.landing_at,
    segment.first_airborne_at,
    segment.last_airborne_at,
    segment.origin_airport_ident,
    COALESCE(origin.iata_code, origin.ident) AS origin_airport,
    origin.name AS origin_airport_name,
    origin.iso_country AS origin_country,
    segment.destination_airport_ident,
    COALESCE(destination.iata_code, destination.ident) AS destination_airport,
    destination.name AS destination_airport_name,
    destination.iso_country AS destination_country,
    segment.origin_evidence,
    segment.destination_evidence,
    segment.observation_count,
    segment.observed_airborne_seconds / 3600.0 AS observed_airborne_hours,
    segment.elapsed_airborne_seconds / 3600.0 AS elapsed_airborne_hours,
    segment.unobserved_seconds / 3600.0 AS unobserved_hours,
    segment.estimated_distance_nm,
    segment.callsigns,
    segment.starts_before_window,
    segment.ends_after_window,
    segment.confidence,
    segment.quality_flags,
    segment.dataset_day_id
FROM aircraft_flight_segment segment
JOIN nl_aircraft_activity activity
  ON activity.dataset_day_id = segment.dataset_day_id
 AND activity.address = segment.address
LEFT JOIN airport origin ON origin.ident = segment.origin_airport_ident
LEFT JOIN airport destination ON destination.ident = segment.destination_airport_ident;

CREATE OR REPLACE VIEW nl_airport_visit AS
SELECT
    visit.utc_date,
    visit.address,
    activity.registration,
    activity.type_code,
    activity.type_description,
    activity.category,
    activity.operator,
    activity.operator_country,
    activity.operator_home_region,
    visit.visit_sequence,
    visit.airport_ident,
    COALESCE(airport.iata_code, airport.ident) AS airport,
    airport.name AS airport_name,
    airport.municipality,
    airport.iso_country AS activity_country,
    heligent_world_region(airport.iso_country) AS activity_region,
    visit.first_evidence_at,
    visit.last_evidence_at,
    visit.arrived_at,
    visit.departed_at,
    visit.ground_observation_count,
    visit.proximity_observation_count,
    visit.ground_time_seconds / 3600.0 AS ground_hours,
    visit.ground_active_time_seconds / 3600.0 AS ground_active_hours,
    visit.closest_distance_nm,
    visit.arrival_evidence,
    visit.departure_evidence,
    visit.open_at_start,
    visit.open_at_end,
    visit.confidence,
    visit.quality_flags,
    visit.dataset_day_id
FROM aircraft_airport_visit visit
JOIN nl_aircraft_activity activity
  ON activity.dataset_day_id = visit.dataset_day_id
 AND activity.address = visit.address
JOIN airport ON airport.ident = visit.airport_ident;

-- A provisional maintenance signal: pair an arrival which remains open at the
-- end of its UTC trace with the next trace-start departure from the same known
-- MRO airport. A 36-hour minimum excludes normal overnight parking. Consumers
-- must use coverage and confidence; this is not proof of maintenance.
CREATE OR REPLACE VIEW nl_mro_stay_candidate AS
WITH mro_airport AS (
    SELECT
        site.airport_ident,
        array_agg(DISTINCT company.name ORDER BY company.name) AS mro_companies
    FROM company_site site
    JOIN company ON company.id = site.company_id
    WHERE site.active
      AND company.active
      AND company.is_mro
      AND site.airport_ident IS NOT NULL
    GROUP BY site.airport_ident
), arrivals AS (
    SELECT visit.*
    FROM aircraft_airport_visit visit
    JOIN mro_airport USING (airport_ident)
    WHERE visit.arrived_at IS NOT NULL
      AND visit.open_at_end
), paired AS (
    SELECT
        arrival.*,
        departure.dataset_day_id AS departure_dataset_day_id,
        departure.utc_date AS departure_utc_date,
        departure.visit_sequence AS departure_visit_sequence,
        departure.first_evidence_at AS departure_first_evidence_at,
        departure.departed_at AS final_departure_at,
        departure.ground_observation_count AS departure_ground_observations,
        departure.confidence AS departure_confidence
    FROM arrivals arrival
    JOIN LATERAL (
        SELECT candidate.*
        FROM aircraft_airport_visit candidate
        WHERE candidate.address = arrival.address
          AND candidate.airport_ident = arrival.airport_ident
          AND candidate.utc_date > arrival.utc_date
          AND candidate.open_at_start
          AND candidate.departed_at IS NOT NULL
          AND candidate.utc_date <= arrival.utc_date + 120
        ORDER BY candidate.departed_at, candidate.dataset_day_id,
                 candidate.visit_sequence
        LIMIT 1
    ) departure ON true
    WHERE departure.departed_at - arrival.arrived_at >= interval '36 hours'
), evidence AS (
    SELECT
        paired.*,
        (paired.final_departure_at::date - paired.arrived_at::date + 1)
            AS expected_coverage_days,
        (
            SELECT count(DISTINCT dataset.utc_date)
            FROM dataset_day dataset
            WHERE dataset.status = 'PROCESSED'
              AND dataset.source_code = 'ADSB_LOL'
              AND dataset.source_selection = 'PREFERRED'
              AND dataset.derivation_version IS NOT NULL
              AND dataset.utc_date BETWEEN paired.arrived_at::date
                                       AND paired.final_departure_at::date
        ) AS processed_coverage_days,
        COALESCE((
            SELECT sum(segment.elapsed_airborne_seconds)
            FROM aircraft_flight_segment segment
            WHERE segment.address = paired.address
              AND segment.utc_date > paired.arrived_at::date
              AND segment.utc_date < paired.final_departure_at::date
        ), 0) AS intermediate_flight_seconds,
        COALESCE((
            SELECT sum(day.active_time_seconds)
            FROM aircraft_day day
            WHERE day.address = paired.address
              AND day.utc_date > paired.arrived_at::date
              AND day.utc_date < paired.final_departure_at::date
        ), 0) AS intermediate_active_seconds
    FROM paired
)
SELECT
    md5(concat_ws('|', evidence.address, evidence.airport_ident,
                  evidence.arrived_at, evidence.final_departure_at)) AS candidate_key,
    evidence.address,
    activity.registration,
    activity.type_code,
    activity.type_description,
    activity.category,
    activity.operator,
    evidence.airport_ident,
    COALESCE(airport.iata_code, airport.ident) AS airport,
    airport.name AS airport_name,
    airport.iso_country AS airport_country,
    mro.mro_companies,
    evidence.arrived_at AS candidate_started_at,
    evidence.final_departure_at AS candidate_ended_at,
    round((extract(epoch FROM evidence.final_departure_at - evidence.arrived_at)
           / 86400.0)::numeric, 2) AS elapsed_days,
    evidence.processed_coverage_days,
    evidence.expected_coverage_days,
    round((evidence.processed_coverage_days::numeric
           / NULLIF(evidence.expected_coverage_days, 0)), 3) AS coverage_ratio,
    evidence.intermediate_flight_seconds / 3600.0 AS intermediate_flight_hours,
    evidence.intermediate_active_seconds / 3600.0 AS intermediate_active_hours,
    CASE
        WHEN evidence.processed_coverage_days = evidence.expected_coverage_days
         AND evidence.intermediate_flight_seconds = 0
         AND evidence.intermediate_active_seconds <= 600
         AND evidence.ground_observation_count > 0
         AND evidence.departure_ground_observations > 0
         AND evidence.confidence <> 'LOW'
         AND evidence.departure_confidence <> 'LOW'
            THEN 'HIGH'
        WHEN evidence.processed_coverage_days::numeric
             / NULLIF(evidence.expected_coverage_days, 0) >= 0.8
         AND evidence.intermediate_flight_seconds = 0
         AND evidence.intermediate_active_seconds <= 1800
            THEN 'MEDIUM'
        ELSE 'LOW'
    END AS confidence,
    array_remove(ARRAY[
        'INFERRED_MRO_AIRPORT_STAY'::text,
        CASE WHEN evidence.processed_coverage_days < evidence.expected_coverage_days
             THEN 'INCOMPLETE_DATE_COVERAGE' END,
        CASE WHEN evidence.intermediate_flight_seconds > 0
             THEN 'INTERMEDIATE_FLIGHT_ACTIVITY' END,
        CASE WHEN evidence.ground_observation_count = 0
                   OR evidence.departure_ground_observations = 0
             THEN 'NO_GROUND_STATE_AT_BOUNDARY' END
    ]::text[], NULL) AS evidence_flags
FROM evidence
JOIN mro_airport mro ON mro.airport_ident = evidence.airport_ident
JOIN airport ON airport.ident = evidence.airport_ident
JOIN nl_aircraft_activity activity
  ON activity.dataset_day_id = evidence.dataset_day_id
 AND activity.address = evidence.address;

CREATE OR REPLACE VIEW nl_maintenance_interval_candidate AS
WITH ordered AS (
    SELECT
        candidate.*,
        lag(candidate.candidate_ended_at) OVER (
            PARTITION BY candidate.address ORDER BY candidate.candidate_started_at
        ) AS previous_candidate_ended_at
    FROM nl_mro_stay_candidate candidate
)
SELECT
    ordered.*,
    round((extract(epoch FROM ordered.candidate_started_at
                   - ordered.previous_candidate_ended_at) / 86400.0)::numeric, 2)
        AS interval_days,
    (
        SELECT count(*)
        FROM aircraft_flight_segment segment
        WHERE segment.address = ordered.address
          AND segment.takeoff_at > ordered.previous_candidate_ended_at
          AND segment.takeoff_at < ordered.candidate_started_at
    ) AS interval_flights,
    (
        SELECT COALESCE(sum(segment.elapsed_airborne_seconds), 0) / 3600.0
        FROM aircraft_flight_segment segment
        WHERE segment.address = ordered.address
          AND segment.takeoff_at > ordered.previous_candidate_ended_at
          AND segment.takeoff_at < ordered.candidate_started_at
    ) AS interval_elapsed_airborne_hours,
    (
        SELECT COALESCE(sum(segment.observed_airborne_seconds), 0) / 3600.0
        FROM aircraft_flight_segment segment
        WHERE segment.address = ordered.address
          AND segment.takeoff_at > ordered.previous_candidate_ended_at
          AND segment.takeoff_at < ordered.candidate_started_at
    ) AS interval_observed_airborne_hours
FROM ordered
WHERE ordered.previous_candidate_ended_at IS NOT NULL;

COMMIT;
