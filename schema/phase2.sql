-- Phase 2 schema proposed from the 2026-08-20 ADSB.lol inspection.
-- PostgreSQL 15+.

BEGIN;

CREATE TYPE dataset_status AS ENUM (
    'NOT_DOWNLOADED',
    'QUEUED',
    'DOWNLOADING',
    'DOWNLOADED',
    'PROCESSING',
    'PROCESSED',
    'FAILED_DOWNLOAD',
    'FAILED_PROCESSING'
);

CREATE TYPE ingestion_job_type AS ENUM ('DOWNLOAD', 'PROCESS', 'REPROCESS');

CREATE TYPE ingestion_job_status AS ENUM (
    'QUEUED',
    'RUNNING',
    'SUCCEEDED',
    'FAILED',
    'INTERRUPTED',
    'CANCELLED'
);

CREATE TYPE aircraft_address_kind AS ENUM ('ICAO', 'NON_ICAO');

CREATE TYPE aircraft_category AS ENUM (
    'UNKNOWN',
    'FIXED_WING',
    'ROTORCRAFT',
    'GLIDER',
    'BALLOON',
    'UAV',
    'GROUND_VEHICLE',
    'OTHER'
);

CREATE TYPE queue_item_status AS ENUM (
    'QUEUED',
    'RUNNING',
    'SUCCEEDED',
    'FAILED',
    'CANCELLED'
);

CREATE TABLE dataset_day (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    utc_date date NOT NULL,
    source_code text NOT NULL DEFAULT 'ADSB_LOL',
    source_selection text NOT NULL DEFAULT 'PREFERRED',
    source_instance text,
    source_repository text,
    source_release_tag text,
    source_url text,
    source_assets jsonb NOT NULL DEFAULT '[]'::jsonb,
    status dataset_status NOT NULL DEFAULT 'NOT_DOWNLOADED',
    queued_at timestamptz,
    download_started_at timestamptz,
    download_finished_at timestamptz,
    processing_started_at timestamptz,
    processing_finished_at timestamptz,
    raw_bytes bigint,
    raw_file_count integer,
    source_aircraft_count integer,
    source_record_count bigint,
    derived_record_count integer,
    airport_presence_record_count integer,
    derived_bytes_estimate bigint,
    download_duration_ms bigint,
    processing_duration_ms bigint,
    peak_disk_bytes bigint,
    raw_deleted_at timestamptz,
    raw_deleted_bytes bigint,
    raw_delete_error text,
    error_stage text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT dataset_day_source_unique
        UNIQUE (utc_date, source_code, source_selection),
    CONSTRAINT dataset_day_id_date_unique UNIQUE (id, utc_date),
    CONSTRAINT dataset_day_source_assets_array
        CHECK (jsonb_typeof(source_assets) = 'array'),
    CONSTRAINT dataset_day_nonnegative_counts CHECK (
        (raw_bytes IS NULL OR raw_bytes >= 0)
        AND (raw_file_count IS NULL OR raw_file_count >= 0)
        AND (source_aircraft_count IS NULL OR source_aircraft_count >= 0)
        AND (source_record_count IS NULL OR source_record_count >= 0)
        AND (derived_record_count IS NULL OR derived_record_count >= 0)
        AND (airport_presence_record_count IS NULL OR airport_presence_record_count >= 0)
        AND (derived_bytes_estimate IS NULL OR derived_bytes_estimate >= 0)
        AND (raw_deleted_bytes IS NULL OR raw_deleted_bytes >= 0)
    )
);

CREATE INDEX dataset_day_status_date_idx ON dataset_day (status, utc_date DESC);

CREATE TABLE ingestion_job (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_day_id bigint NOT NULL REFERENCES dataset_day (id) ON DELETE CASCADE,
    job_type ingestion_job_type NOT NULL,
    status ingestion_job_status NOT NULL DEFAULT 'QUEUED',
    attempt integer NOT NULL DEFAULT 1,
    progress_percent numeric(5, 2),
    status_message text,
    worker_id text,
    queued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    started_at timestamptz,
    heartbeat_at timestamptz,
    finished_at timestamptz,
    error_message text,
    CONSTRAINT ingestion_job_attempt_positive CHECK (attempt > 0),
    CONSTRAINT ingestion_job_progress_range CHECK (
        progress_percent IS NULL OR progress_percent BETWEEN 0 AND 100
    )
);

-- Prevent duplicate queued/running work for the same date while retaining job history.
CREATE UNIQUE INDEX ingestion_job_one_active_per_day_idx
    ON ingestion_job (dataset_day_id)
    WHERE status IN ('QUEUED', 'RUNNING');

CREATE INDEX ingestion_job_queue_idx ON ingestion_job (status, queued_at, id);

-- User-requested work is separate from per-stage ingestion_job history. This
-- lets one sequential worker claim dates without an active job blocking the
-- DOWNLOAD and PROCESS records created by the ingestion service itself.
CREATE TABLE ingestion_queue (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    utc_date date NOT NULL,
    requested_action text NOT NULL DEFAULT 'INGEST',
    keep_raw boolean NOT NULL DEFAULT false,
    raw_source text NOT NULL DEFAULT 'DIRECT',
    status queue_item_status NOT NULL DEFAULT 'QUEUED',
    attempts integer NOT NULL DEFAULT 0,
    worker_id text,
    requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    started_at timestamptz,
    heartbeat_at timestamptz,
    finished_at timestamptz,
    result jsonb,
    error_message text,
    CONSTRAINT ingestion_queue_action_check
        CHECK (requested_action IN ('INGEST', 'REPROCESS')),
    CONSTRAINT ingestion_queue_raw_source_check
        CHECK (raw_source IN ('DIRECT', 'PI')),
    CONSTRAINT ingestion_queue_attempts_nonnegative CHECK (attempts >= 0)
);

CREATE UNIQUE INDEX ingestion_queue_one_active_date_idx
    ON ingestion_queue (utc_date)
    WHERE status IN ('QUEUED', 'RUNNING');
CREATE INDEX ingestion_queue_claim_idx
    ON ingestion_queue (status, requested_at, id);
CREATE INDEX ingestion_queue_date_history_idx
    ON ingestion_queue (utc_date, id DESC);

CREATE TABLE aircraft (
    address varchar(7) PRIMARY KEY,
    address_kind aircraft_address_kind NOT NULL,
    icao_hex char(6) GENERATED ALWAYS AS (
        CASE WHEN address ~ '^[0-9a-f]{6}$' THEN address::char(6) END
    ) STORED,
    registration text,
    callsign_last_seen text,
    type_code text,
    type_description text,
    owner_operator text,
    manufacture_year smallint,
    db_flags integer,
    country text,
    military_flag boolean,
    first_seen_date date NOT NULL,
    last_seen_date date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT aircraft_address_lowercase CHECK (address = lower(address)),
    CONSTRAINT aircraft_address_shape CHECK (
        (address_kind = 'ICAO' AND address ~ '^[0-9a-f]{6}$')
        OR (address_kind = 'NON_ICAO' AND address ~ '^~[0-9a-f]{6}$')
    ),
    CONSTRAINT aircraft_seen_date_order CHECK (first_seen_date <= last_seen_date),
    CONSTRAINT aircraft_manufacture_year_range CHECK (
        manufacture_year IS NULL OR manufacture_year BETWEEN 1900 AND 2200
    )
);

CREATE UNIQUE INDEX aircraft_icao_hex_unique_idx
    ON aircraft (icao_hex)
    WHERE icao_hex IS NOT NULL;
CREATE INDEX aircraft_registration_idx ON aircraft (upper(registration));
CREATE INDEX aircraft_type_code_idx ON aircraft (type_code);
CREATE INDEX aircraft_last_seen_idx ON aircraft (last_seen_date DESC);

CREATE TABLE aircraft_type_classification (
    type_code text PRIMARY KEY,
    category aircraft_category NOT NULL DEFAULT 'UNKNOWN',
    classification_source text NOT NULL,
    confidence numeric(4, 3),
    notes text,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT aircraft_type_confidence_range CHECK (
        confidence IS NULL OR confidence BETWEEN 0 AND 1
    )
);

CREATE TABLE reference_dataset (
    code text PRIMARY KEY,
    source_url text NOT NULL,
    sha256 char(64) NOT NULL,
    downloaded_at timestamptz NOT NULL,
    row_count integer NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT reference_dataset_nonnegative_rows CHECK (row_count >= 0),
    CONSTRAINT reference_dataset_metadata_object
        CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE TABLE airport (
    ident text PRIMARY KEY,
    airport_type text NOT NULL,
    name text NOT NULL,
    latitude_deg double precision NOT NULL,
    longitude_deg double precision NOT NULL,
    elevation_ft integer,
    continent text,
    iso_country text,
    iso_region text,
    municipality text,
    scheduled_service boolean NOT NULL DEFAULT false,
    gps_code text,
    iata_code text,
    local_code text,
    source_code text NOT NULL REFERENCES reference_dataset (code),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT airport_coordinate_range CHECK (
        latitude_deg BETWEEN -90 AND 90
        AND longitude_deg BETWEEN -180 AND 180
    )
);

CREATE INDEX airport_gps_code_idx ON airport (gps_code);
CREATE INDEX airport_iata_code_idx ON airport (iata_code);
CREATE INDEX airport_country_type_idx ON airport (iso_country, airport_type);

CREATE TABLE aircraft_day (
    dataset_day_id bigint NOT NULL,
    utc_date date NOT NULL,
    address varchar(7) NOT NULL REFERENCES aircraft (address),
    registration text,
    type_code text,
    type_description text,
    owner_operator text,
    manufacture_year smallint,
    db_flags integer,
    trace_format_version text,
    callsigns text[] NOT NULL DEFAULT ARRAY[]::text[],
    position_source_types text[] NOT NULL DEFAULT ARRAY[]::text[],
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    observation_count integer NOT NULL,
    position_count integer NOT NULL,
    ground_observation_count integer NOT NULL DEFAULT 0,
    min_altitude_ft integer,
    max_altitude_ft integer,
    max_ground_speed_knots real,
    first_latitude double precision,
    first_longitude double precision,
    last_latitude double precision,
    last_longitude double precision,
    time_observed_seconds integer NOT NULL,
    active_time_seconds integer NOT NULL,
    airborne_time_seconds integer NOT NULL,
    ground_active_time_seconds integer NOT NULL,
    distinct_airports integer NOT NULL DEFAULT 0,
    airport_presence_count integer NOT NULL DEFAULT 0,
    estimated_distance_nm double precision,
    source_trace_gzip_bytes integer NOT NULL,
    source_trace_json_bytes integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (dataset_day_id, address),
    CONSTRAINT aircraft_day_dataset_date_fk
        FOREIGN KEY (dataset_day_id, utc_date)
        REFERENCES dataset_day (id, utc_date)
        ON DELETE CASCADE,
    CONSTRAINT aircraft_day_time_order CHECK (first_seen_at <= last_seen_at),
    CONSTRAINT aircraft_day_nonnegative_metrics CHECK (
        observation_count >= 0
        AND position_count >= 0
        AND ground_observation_count >= 0
        AND time_observed_seconds >= 0
        AND active_time_seconds >= 0
        AND airborne_time_seconds >= 0
        AND ground_active_time_seconds >= 0
        AND distinct_airports >= 0
        AND airport_presence_count >= 0
        AND source_trace_gzip_bytes >= 0
        AND source_trace_json_bytes >= 0
        AND (estimated_distance_nm IS NULL OR estimated_distance_nm >= 0)
    ),
    CONSTRAINT aircraft_day_coordinate_range CHECK (
        (first_latitude IS NULL OR first_latitude BETWEEN -90 AND 90)
        AND (last_latitude IS NULL OR last_latitude BETWEEN -90 AND 90)
        AND (first_longitude IS NULL OR first_longitude BETWEEN -180 AND 180)
        AND (last_longitude IS NULL OR last_longitude BETWEEN -180 AND 180)
    )
);

CREATE INDEX aircraft_day_date_type_idx ON aircraft_day (utc_date, type_code);
CREATE INDEX aircraft_day_date_observations_idx
    ON aircraft_day (utc_date, observation_count DESC);
CREATE INDEX aircraft_day_address_date_idx ON aircraft_day (address, utc_date DESC);
CREATE INDEX aircraft_day_registration_idx ON aircraft_day (upper(registration));
CREATE INDEX aircraft_day_callsigns_gin_idx ON aircraft_day USING gin (callsigns);

CREATE TABLE aircraft_airport_day (
    dataset_day_id bigint NOT NULL,
    utc_date date NOT NULL,
    airport_ident text NOT NULL REFERENCES airport (ident),
    address varchar(7) NOT NULL,
    is_primary_airport boolean NOT NULL DEFAULT false,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    presence_count integer NOT NULL,
    ground_observation_count integer NOT NULL,
    ground_time_seconds integer NOT NULL,
    ground_active_time_seconds integer NOT NULL,
    closest_distance_nm real NOT NULL,
    inferred_endpoint_count integer NOT NULL DEFAULT 0,
    arrival_count integer NOT NULL DEFAULT 0,
    departure_count integer NOT NULL DEFAULT 0,
    link_method text NOT NULL DEFAULT 'GROUND',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (dataset_day_id, airport_ident, address),
    CONSTRAINT aircraft_airport_day_aircraft_day_fk
        FOREIGN KEY (dataset_day_id, address)
        REFERENCES aircraft_day (dataset_day_id, address)
        ON DELETE CASCADE,
    CONSTRAINT aircraft_airport_day_dataset_date_fk
        FOREIGN KEY (dataset_day_id, utc_date)
        REFERENCES dataset_day (id, utc_date)
        ON DELETE CASCADE,
    CONSTRAINT aircraft_airport_day_time_order CHECK (first_seen_at <= last_seen_at),
    CONSTRAINT aircraft_airport_day_nonnegative_metrics CHECK (
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
    CONSTRAINT aircraft_airport_day_link_method_check CHECK (
        link_method IN ('GROUND', 'INFERRED_ENDPOINT', 'GROUND_AND_ENDPOINT')
    )
);

CREATE INDEX aircraft_airport_day_hub_idx
    ON aircraft_airport_day (airport_ident, utc_date, address);
CREATE INDEX aircraft_airport_day_tail_idx
    ON aircraft_airport_day (address, utc_date DESC);
CREATE UNIQUE INDEX aircraft_airport_day_one_primary_idx
    ON aircraft_airport_day (dataset_day_id, address)
    WHERE is_primary_airport;

-- Cheap tail/type metrics. Hours are observation-derived estimates, not logbook time.
CREATE VIEW aircraft_type_day_metrics AS
SELECT
    utc_date,
    COALESCE(type_code, 'UNKNOWN') AS type_code,
    count(*) AS unique_aircraft,
    sum(observation_count) AS observations,
    sum(active_time_seconds) / 3600.0 AS active_hours,
    sum(airborne_time_seconds) / 3600.0 AS airborne_hours,
    sum(ground_active_time_seconds) / 3600.0 AS ground_active_hours,
    sum(distinct_airports) AS airport_links
FROM aircraft_day
GROUP BY utc_date, COALESCE(type_code, 'UNKNOWN');

-- One row per hub/day. Daily tail hours are attributed only to each tail's
-- primary observed airport, avoiding double-counting tails linked to two hubs.
CREATE VIEW airport_day_metrics AS
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

CREATE VIEW airport_day_type_metrics AS
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

-- Idempotent date replacement contract for the Phase 2 worker:
--
-- BEGIN;
--   DELETE FROM aircraft_day WHERE dataset_day_id = :dataset_day_id;
--   -- Parse into a temporary/staging table, upsert aircraft, then INSERT aircraft_day.
--   -- Update dataset_day counts/status only after every trace parsed successfully.
-- COMMIT;
--
-- A parse failure rolls back all derived rows and leaves raw files in place.
