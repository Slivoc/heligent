BEGIN;

CREATE TABLE IF NOT EXISTS aircraft_registry_source (
    code text PRIMARY KEY,
    name text NOT NULL,
    authority_code text NOT NULL,
    country_code char(2) NOT NULL,
    source_url text NOT NULL,
    data_license text NOT NULL,
    attribution text NOT NULL,
    priority smallint NOT NULL DEFAULT 100,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT aircraft_registry_source_priority_positive CHECK (priority > 0)
);

INSERT INTO aircraft_registry_source (
    code, name, authority_code, country_code, source_url, data_license,
    attribution, priority
)
VALUES (
    'TC_CCAR',
    'Transport Canada Canadian Civil Aircraft Register',
    'TRANSPORT_CANADA',
    'CA',
    'https://wwwapps.tc.gc.ca/Saf-Sec-Sur/2/CCARCS-RIACC/DDZip.aspx?lang=eng',
    'Government of Canada Open Data Licence Agreement for Unrestricted Use',
    'Includes data provided by the Government of Canada; no endorsement is implied.',
    10
)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    authority_code = EXCLUDED.authority_code,
    country_code = EXCLUDED.country_code,
    source_url = EXCLUDED.source_url,
    data_license = EXCLUDED.data_license,
    attribution = EXCLUDED.attribution,
    priority = EXCLUDED.priority,
    active = true,
    updated_at = clock_timestamp();

INSERT INTO aircraft_registry_source (
    code, name, authority_code, country_code, source_url, data_license,
    attribution, priority
)
VALUES (
    'CASA_AIRCRAFT_REGISTER',
    'CASA Australian Civil Aircraft Register',
    'AU_CASA',
    'AU',
    'https://www.casa.gov.au/aircraft/aircraft-registration/data-files-registered-aircraft',
    'CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)',
    'Source: Civil Aviation Safety Authority (Australia), Australian Civil Aircraft Register; licensed under CC BY 4.0. Changes: normalized fields and category mappings.',
    10
)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    authority_code = EXCLUDED.authority_code,
    country_code = EXCLUDED.country_code,
    source_url = EXCLUDED.source_url,
    data_license = EXCLUDED.data_license,
    attribution = EXCLUDED.attribution,
    priority = EXCLUDED.priority,
    active = true,
    updated_at = clock_timestamp();

-- AS21 was previously inferred as rotorcraft from one stale ADS-B description.
-- ICAO Doc 8643 identifies AS21 as the Schleicher ASK-21Mi, and the matched FAA
-- airframes independently classify it as a glider. Preserve manual curation.
INSERT INTO aircraft_type_classification (
    type_code, category, classification_source, confidence, notes
)
VALUES (
    'AS21', 'GLIDER'::aircraft_category, 'ICAO_DOC_8643', 1.000,
    'Schleicher ASK-21Mi; replaces a stale description-based rotorcraft inference'
)
ON CONFLICT (type_code) DO UPDATE SET
    category = EXCLUDED.category,
    classification_source = EXCLUDED.classification_source,
    confidence = EXCLUDED.confidence,
    notes = EXCLUDED.notes,
    updated_at = clock_timestamp()
WHERE aircraft_type_classification.classification_source =
      'PHASE4_DESCRIPTION_HEURISTIC';

INSERT INTO aircraft_registry_source (
    code, name, authority_code, country_code, source_url, data_license,
    attribution, priority
)
VALUES (
    'FAA_AIRCRAFT_REGISTRY',
    'FAA Releasable Aircraft Database',
    'US_FAA',
    'US',
    'https://registry.faa.gov/database/ReleasableAircraft.zip',
    'FAA public releasable aircraft database; no separate licence stated',
    'Source: Federal Aviation Administration Civil Aviation Registry.',
    10
)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    authority_code = EXCLUDED.authority_code,
    country_code = EXCLUDED.country_code,
    source_url = EXCLUDED.source_url,
    data_license = EXCLUDED.data_license,
    attribution = EXCLUDED.attribution,
    priority = EXCLUDED.priority,
    active = true,
    updated_at = clock_timestamp();

CREATE TABLE IF NOT EXISTS aircraft_registry_import_batch (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_code text NOT NULL REFERENCES aircraft_registry_source (code),
    snapshot_date date NOT NULL,
    downloaded_at timestamptz NOT NULL,
    source_file_name text NOT NULL,
    source_sha256 char(64) NOT NULL,
    source_bytes bigint NOT NULL,
    row_count integer NOT NULL,
    status text NOT NULL DEFAULT 'IMPORTED',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT aircraft_registry_import_batch_status CHECK (
        status IN ('IMPORTED', 'SUPERSEDED', 'FAILED')
    ),
    CONSTRAINT aircraft_registry_import_batch_nonnegative CHECK (
        source_bytes >= 0 AND row_count >= 0
    ),
    CONSTRAINT aircraft_registry_import_batch_metadata_object CHECK (
        jsonb_typeof(metadata) = 'object'
    ),
    UNIQUE (source_code, source_sha256)
);

CREATE INDEX IF NOT EXISTS aircraft_registry_import_source_date_idx
    ON aircraft_registry_import_batch (source_code, snapshot_date DESC, id DESC)
    WHERE status = 'IMPORTED';

CREATE TABLE IF NOT EXISTS aircraft_registry_record (
    import_batch_id bigint NOT NULL
        REFERENCES aircraft_registry_import_batch (id) ON DELETE CASCADE,
    registration text NOT NULL,
    address varchar(7),
    manufacturer text NOT NULL,
    model text NOT NULL,
    serial_number text,
    registry_category text NOT NULL,
    official_category aircraft_category NOT NULL,
    registration_sub_type text,
    registration_status text,
    issue_date date,
    effective_date date NOT NULL,
    ineffective_date date,
    modified_date date,
    manufacture_date date,
    base_country text,
    base_region text,
    base_location text,
    type_certificate_number text,
    engine_category text,
    number_of_engines smallint,
    number_of_seats smallint,
    official_type_code varchar(16),
    registered_operator text,
    operator_effective_date date,
    operator_country text,
    operator_geographic_region text,
    source_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (import_batch_id, registration),
    CONSTRAINT aircraft_registry_record_address_lowercase CHECK (
        address IS NULL OR (
            address = lower(address) AND address ~ '^[0-9a-f]{6}$'
        )
    ),
    CONSTRAINT aircraft_registry_record_registration_uppercase CHECK (
        registration = upper(registration)
    ),
    CONSTRAINT aircraft_registry_record_date_order CHECK (
        ineffective_date IS NULL OR ineffective_date >= effective_date
    ),
    CONSTRAINT aircraft_registry_record_source_data_object CHECK (
        jsonb_typeof(source_data) = 'object'
    )
);

ALTER TABLE aircraft_registry_record ALTER COLUMN address DROP NOT NULL;
ALTER TABLE aircraft_registry_record
    DROP CONSTRAINT IF EXISTS aircraft_registry_record_import_batch_id_address_key;
ALTER TABLE aircraft_registry_record
    ADD COLUMN IF NOT EXISTS official_type_code varchar(16);
ALTER TABLE aircraft_registry_record
    ADD COLUMN IF NOT EXISTS registered_operator text;
ALTER TABLE aircraft_registry_record
    ADD COLUMN IF NOT EXISTS operator_effective_date date;
ALTER TABLE aircraft_registry_record
    ADD COLUMN IF NOT EXISTS operator_country text;
ALTER TABLE aircraft_registry_record
    ADD COLUMN IF NOT EXISTS operator_geographic_region text;
UPDATE aircraft_registry_record
SET operator_geographic_region = heligent_world_region(operator_country)
WHERE operator_geographic_region IS DISTINCT FROM
      heligent_world_region(operator_country);

CREATE UNIQUE INDEX IF NOT EXISTS aircraft_registry_record_batch_address_uidx
    ON aircraft_registry_record (import_batch_id, address)
    WHERE address IS NOT NULL;

CREATE INDEX IF NOT EXISTS aircraft_registry_record_address_dates_idx
    ON aircraft_registry_record (
        address, effective_date, ineffective_date, import_batch_id
    );

CREATE OR REPLACE VIEW aircraft_registry_latest AS
WITH latest_batch AS (
    SELECT DISTINCT ON (b.source_code)
        b.source_code,
        b.id,
        b.snapshot_date,
        b.downloaded_at
    FROM aircraft_registry_import_batch b
    WHERE b.status = 'IMPORTED'
    ORDER BY b.source_code, b.snapshot_date DESC, b.id DESC
)
SELECT
    s.code AS source_code,
    s.name AS source_name,
    s.authority_code,
    s.country_code,
    s.priority,
    lb.snapshot_date,
    lb.downloaded_at,
    r.*
FROM latest_batch lb
JOIN aircraft_registry_source s ON s.code = lb.source_code AND s.active
JOIN aircraft_registry_record r ON r.import_batch_id = lb.id;

CREATE OR REPLACE VIEW aircraft_registry_resolved_current AS
WITH resolved_address AS (
    SELECT DISTINCT ON (current_registry.address)
        current_registry.*
    FROM aircraft_registry_latest current_registry
    WHERE current_registry.address IS NOT NULL
    ORDER BY current_registry.address, current_registry.priority,
        current_registry.snapshot_date DESC, current_registry.import_batch_id DESC
)
SELECT * FROM resolved_address
UNION ALL
SELECT *
FROM aircraft_registry_latest
WHERE address IS NULL;

-- Identity analytics execute several independent rollups per request. Cache the
-- latest per-address authority decision so each rollup does not repeatedly sort
-- every national registry record. Imports refresh this cache transactionally.
DROP MATERIALIZED VIEW IF EXISTS aircraft_registry_resolved_cache CASCADE;
CREATE MATERIALIZED VIEW aircraft_registry_resolved_cache AS
SELECT *
FROM aircraft_registry_resolved_current;

CREATE UNIQUE INDEX aircraft_registry_resolved_cache_address_idx
    ON aircraft_registry_resolved_cache (address)
    WHERE address IS NOT NULL;
CREATE UNIQUE INDEX aircraft_registry_resolved_cache_registration_only_idx
    ON aircraft_registry_resolved_cache (
        regexp_replace(upper(registration), '[^A-Z0-9]', '', 'g')
    ) WHERE address IS NULL;
ANALYZE aircraft_registry_resolved_cache;

-- One provenance-bearing operator-claim surface for individual-tail displays.
-- Consumers choose the best matching claim per tail; the view deliberately
-- retains competing claims rather than merging company identities.
CREATE OR REPLACE VIEW current_aircraft_operator_claim AS
SELECT
    cca.company_name AS operator,
    cca.aircraft_address,
    cca.reported_aircraft_address,
    upper(NULLIF(cca.registration, '')) AS registration,
    regexp_replace(upper(NULLIF(cca.registration, '')), '[^A-Z0-9]', '', 'g')
        AS registration_key,
    cca.source_code AS operator_source_code,
    cca.confidence,
    'CRM_ASSIGNMENT'::text AS claim_kind
FROM current_company_aircraft cca
WHERE cca.assignment_role = 'OPERATOR'
UNION ALL
SELECT
    registered_operator,
    address,
    NULL::varchar,
    upper(registration),
    regexp_replace(upper(registration), '[^A-Z0-9]', '', 'g'),
    source_code,
    1.000::numeric,
    'REGISTRY_REGISTERED_OPERATOR'::text
FROM aircraft_registry_resolved_cache
WHERE registered_operator IS NOT NULL
  AND operator_effective_date <= CURRENT_DATE
  AND (ineffective_date IS NULL OR ineffective_date >= CURRENT_DATE);

CREATE OR REPLACE VIEW aircraft_day_identity AS
WITH matched AS (
SELECT
    ad.*,
    source_class.category AS source_category,
    COALESCE(reg_address.import_batch_id, reg_registration.import_batch_id)
        AS registry_match_id,
    COALESCE(reg_address.registration, reg_registration.registration)
        AS registry_registration,
    COALESCE(reg_address.manufacturer, reg_registration.manufacturer)
        AS registry_manufacturer,
    COALESCE(reg_address.model, reg_registration.model) AS registry_model,
    COALESCE(reg_address.official_category, reg_registration.official_category)
        AS registry_category,
    COALESCE(reg_address.official_type_code, reg_registration.official_type_code)
        AS registry_type_code,
    COALESCE(reg_address.source_code, reg_registration.source_code)
        AS registry_source_code,
    COALESCE(reg_address.authority_code, reg_registration.authority_code)
        AS registry_authority_code,
    COALESCE(reg_address.snapshot_date, reg_registration.snapshot_date)
        AS registry_snapshot_date,
    COALESCE(reg_address.serial_number, reg_registration.serial_number)
        AS registry_serial_number,
    COALESCE(reg_address.registered_operator, reg_registration.registered_operator)
        AS registry_operator,
    COALESCE(reg_address.operator_effective_date,
             reg_registration.operator_effective_date)
        AS registry_operator_effective_date,
    COALESCE(reg_address.operator_country, reg_registration.operator_country)
        AS registry_operator_country,
    COALESCE(reg_address.operator_geographic_region,
             reg_registration.operator_geographic_region)
        AS registry_operator_geographic_region
FROM aircraft_day ad
LEFT JOIN aircraft_type_classification source_class
  ON source_class.type_code = ad.type_code
LEFT JOIN aircraft_registry_resolved_cache reg_address
  ON reg_address.address = ad.address
 AND ad.utc_date >= reg_address.effective_date
 AND (reg_address.ineffective_date IS NULL
      OR ad.utc_date <= reg_address.ineffective_date)
LEFT JOIN aircraft_registry_resolved_cache reg_registration
  ON reg_address.import_batch_id IS NULL
 AND reg_registration.address IS NULL
 AND regexp_replace(upper(reg_registration.registration), '[^A-Z0-9]', '', 'g') =
     regexp_replace(upper(ad.registration), '[^A-Z0-9]', '', 'g')
 AND ad.registration IS NOT NULL
 AND ad.utc_date >= reg_registration.effective_date
 AND (reg_registration.ineffective_date IS NULL
      OR ad.utc_date <= reg_registration.ineffective_date)
)
SELECT
    dataset_day_id,
    utc_date,
    address,
    COALESCE(registry_registration, registration) AS registration,
    CASE
        WHEN registry_match_id IS NULL THEN type_code
        WHEN registry_type_code IS NOT NULL THEN registry_type_code
        WHEN (
              registration IS NOT NULL
              AND regexp_replace(upper(registry_registration), '[^A-Z0-9]', '', 'g')
                  IS DISTINCT FROM
                  regexp_replace(upper(registration), '[^A-Z0-9]', '', 'g')
             )
          OR (
              source_category IS NOT NULL
              AND source_category <> 'UNKNOWN'
              AND source_category <> registry_category
          )
            THEN NULL
        ELSE type_code
    END AS type_code,
    CASE
        WHEN registry_match_id IS NOT NULL
            THEN concat_ws(' ', registry_manufacturer, registry_model)
        ELSE type_description
    END AS type_description,
    owner_operator, manufacture_year, db_flags, trace_format_version, callsigns,
    position_source_types, first_seen_at, last_seen_at, observation_count,
    position_count, ground_observation_count, min_altitude_ft, max_altitude_ft,
    max_ground_speed_knots, first_latitude, first_longitude, last_latitude,
    last_longitude, time_observed_seconds, active_time_seconds,
    airborne_time_seconds, ground_active_time_seconds, distinct_airports,
    airport_presence_count, estimated_distance_nm, source_trace_gzip_bytes,
    source_trace_json_bytes, created_at,
    registration AS source_registration,
    type_code AS source_type_code,
    type_description AS source_type_description,
    COALESCE(registry_category, source_category, 'UNKNOWN')
        AS resolved_category,
    registry_source_code AS identity_source_code,
    registry_authority_code AS identity_authority_code,
    registry_snapshot_date AS identity_snapshot_date,
    registry_serial_number,
    registry_operator,
    registry_operator_effective_date,
    registry_operator_country,
    registry_operator_geographic_region,
    CASE
        WHEN registry_match_id IS NULL THEN 'SOURCE_ONLY'
        WHEN registration IS NOT NULL
         AND regexp_replace(upper(registry_registration), '[^A-Z0-9]', '', 'g')
             IS DISTINCT FROM
             regexp_replace(upper(registration), '[^A-Z0-9]', '', 'g')
         AND source_category IS NOT NULL
         AND source_category <> 'UNKNOWN'
         AND source_category <> registry_category
            THEN 'REGISTRATION_AND_CATEGORY_CONFLICT'
        WHEN registration IS NOT NULL
         AND regexp_replace(upper(registry_registration), '[^A-Z0-9]', '', 'g')
             IS DISTINCT FROM
             regexp_replace(upper(registration), '[^A-Z0-9]', '', 'g')
            THEN 'REGISTRATION_CONFLICT'
        WHEN source_category IS NOT NULL
         AND source_category <> 'UNKNOWN'
         AND source_category <> registry_category
            THEN 'CATEGORY_CONFLICT'
        WHEN registration IS NULL THEN 'REGISTRY_ENRICHED'
        ELSE 'REGISTRY_CATEGORY_VERIFIED'
    END AS identity_status
FROM matched;

CREATE OR REPLACE VIEW aircraft_identity_conflict AS
SELECT *
FROM aircraft_day_identity
WHERE identity_status IN (
    'REGISTRATION_CONFLICT',
    'CATEGORY_CONFLICT',
    'REGISTRATION_AND_CATEGORY_CONFLICT'
);

COMMIT;
