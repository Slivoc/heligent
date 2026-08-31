BEGIN;

-- Natural-language queries target these semantic relations only. They expose
-- stable aviation concepts and hide ingestion internals from the SQL planner.

CREATE OR REPLACE VIEW nl_operator_claim AS
SELECT
    c.name AS operator,
    c.country_code::text AS operator_country,
    caa.geographic_region AS operator_region,
    caa.aircraft_address,
    caa.reported_aircraft_address,
    upper(NULLIF(caa.registration, '')) AS registration,
    caa.source_code AS operator_source_code,
    caa.confidence,
    'CRM_ASSIGNMENT'::text AS evidence_kind
FROM company_aircraft_assignment caa
JOIN company c ON c.id = caa.company_id
WHERE caa.active
  AND c.active
  AND caa.assignment_role = 'OPERATOR'
  AND (caa.valid_from IS NULL OR caa.valid_from <= CURRENT_DATE)
  AND (caa.valid_to IS NULL OR caa.valid_to >= CURRENT_DATE)
UNION ALL
SELECT
    registered_operator,
    operator_country,
    operator_geographic_region,
    address,
    NULL::varchar,
    upper(registration),
    source_code,
    1.000::numeric,
    'REGISTRY_REGISTERED_OPERATOR'::text
FROM aircraft_registry_resolved_cache
WHERE registered_operator IS NOT NULL
  AND operator_effective_date <= CURRENT_DATE
  AND (ineffective_date IS NULL OR ineffective_date >= CURRENT_DATE);

CREATE TABLE IF NOT EXISTS nl_best_aircraft_operator (
    address varchar(7) PRIMARY KEY REFERENCES aircraft (address) ON DELETE CASCADE,
    operator text NOT NULL,
    operator_country text,
    operator_region text,
    operator_source_code text NOT NULL,
    confidence numeric(4, 3),
    evidence_kind text NOT NULL,
    refreshed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS nl_best_aircraft_operator_name_idx
    ON nl_best_aircraft_operator (operator);
CREATE INDEX IF NOT EXISTS nl_best_aircraft_operator_region_idx
    ON nl_best_aircraft_operator (operator_region, operator);

CREATE OR REPLACE FUNCTION heligent_refresh_nl_operator_cache()
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    TRUNCATE nl_best_aircraft_operator;
    INSERT INTO nl_best_aircraft_operator (
        address, operator, operator_country, operator_region,
        operator_source_code, confidence, evidence_kind
    )
    WITH candidates AS (
        SELECT
            claim.aircraft_address AS address,
            claim.operator,
            claim.operator_country,
            claim.operator_region,
            claim.operator_source_code,
            claim.confidence,
            claim.evidence_kind,
            0 AS match_priority
        FROM nl_operator_claim claim
        WHERE claim.aircraft_address IS NOT NULL
        UNION ALL
        SELECT
            claim.reported_aircraft_address,
            claim.operator,
            claim.operator_country,
            claim.operator_region,
            claim.operator_source_code,
            claim.confidence,
            claim.evidence_kind,
            1
        FROM nl_operator_claim claim
        WHERE claim.reported_aircraft_address IS NOT NULL
          AND claim.reported_aircraft_address ~ '^[0-9a-f]{6}$'
        UNION ALL
        SELECT
            aircraft.address,
            claim.operator,
            claim.operator_country,
            claim.operator_region,
            claim.operator_source_code,
            claim.confidence,
            claim.evidence_kind,
            2
        FROM nl_operator_claim claim
        JOIN aircraft
          ON upper(aircraft.registration) = claim.registration
        WHERE claim.registration IS NOT NULL
    )
    SELECT DISTINCT ON (address)
        address,
        operator,
        operator_country,
        operator_region,
        operator_source_code,
        confidence,
        evidence_kind
    FROM candidates
    WHERE address IS NOT NULL
    ORDER BY address, match_priority, confidence DESC NULLS LAST,
        operator_source_code, operator;
    ANALYZE nl_best_aircraft_operator;
END;
$$;

SELECT heligent_refresh_nl_operator_cache();

CREATE TABLE IF NOT EXISTS nl_aircraft_activity_cache (
    dataset_day_id bigint NOT NULL REFERENCES dataset_day (id) ON DELETE CASCADE,
    utc_date date NOT NULL,
    address varchar(7) NOT NULL REFERENCES aircraft (address) ON DELETE CASCADE,
    registration text,
    type_code text,
    type_description text,
    category text NOT NULL,
    observations integer NOT NULL,
    active_hours numeric NOT NULL,
    airborne_hours numeric NOT NULL,
    ground_active_hours numeric NOT NULL,
    time_observed_hours numeric NOT NULL,
    distinct_airports integer NOT NULL,
    estimated_distance_nm double precision,
    identity_source_code text,
    identity_status text NOT NULL,
    registry_operator text,
    registry_operator_country text,
    registry_operator_region text,
    registry_operator_source_code text,
    refreshed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (dataset_day_id, address)
);

ALTER TABLE nl_aircraft_activity_cache
    ADD COLUMN IF NOT EXISTS registry_operator text,
    ADD COLUMN IF NOT EXISTS registry_operator_country text,
    ADD COLUMN IF NOT EXISTS registry_operator_region text,
    ADD COLUMN IF NOT EXISTS registry_operator_source_code text;

CREATE INDEX IF NOT EXISTS nl_aircraft_activity_cache_date_type_idx
    ON nl_aircraft_activity_cache (utc_date, type_code, address);
CREATE INDEX IF NOT EXISTS nl_aircraft_activity_cache_date_category_idx
    ON nl_aircraft_activity_cache (utc_date, category, address);
CREATE INDEX IF NOT EXISTS nl_aircraft_activity_cache_address_date_idx
    ON nl_aircraft_activity_cache (address, utc_date DESC);

CREATE OR REPLACE FUNCTION heligent_refresh_nl_activity_cache(
    rebuild boolean DEFAULT false
)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    missing_dataset_day_id bigint;
BEGIN
    IF rebuild THEN
        TRUNCATE nl_aircraft_activity_cache;
    END IF;

    FOR missing_dataset_day_id IN
        SELECT dataset.id
        FROM dataset_day dataset
        WHERE EXISTS (
            SELECT 1
            FROM aircraft_day source
            WHERE source.dataset_day_id = dataset.id
        )
          AND NOT EXISTS (
            SELECT 1
            FROM nl_aircraft_activity_cache cached
            WHERE cached.dataset_day_id = dataset.id
        )
    LOOP
        INSERT INTO nl_aircraft_activity_cache (
            dataset_day_id, utc_date, address, registration, type_code,
            type_description, category, observations, active_hours,
            airborne_hours, ground_active_hours, time_observed_hours,
            distinct_airports, estimated_distance_nm, identity_source_code,
            identity_status, registry_operator, registry_operator_country,
            registry_operator_region, registry_operator_source_code
        )
        SELECT
            identity.dataset_day_id,
            identity.utc_date,
            identity.address,
            identity.registration,
            identity.type_code,
            identity.type_description,
            COALESCE(identity.resolved_category::text, 'UNKNOWN'),
            identity.observation_count,
            identity.active_time_seconds / 3600.0,
            identity.airborne_time_seconds / 3600.0,
            identity.ground_active_time_seconds / 3600.0,
            identity.time_observed_seconds / 3600.0,
            identity.distinct_airports,
            identity.estimated_distance_nm,
            identity.identity_source_code,
            identity.identity_status,
            identity.registry_operator,
            identity.registry_operator_country,
            identity.registry_operator_geographic_region,
            identity.identity_source_code
        FROM aircraft_day_identity identity
        WHERE identity.dataset_day_id = missing_dataset_day_id
          AND COALESCE(identity.resolved_category::text, 'UNKNOWN') <> 'GROUND_VEHICLE'
        ON CONFLICT (dataset_day_id, address) DO NOTHING;
    END LOOP;
    ANALYZE nl_aircraft_activity_cache;
END;
$$;

CREATE OR REPLACE FUNCTION heligent_refresh_nl_activity_cache_for_registry_batch(
    target_batch_id bigint
)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    CREATE TEMP TABLE IF NOT EXISTS heligent_nl_impacted_address (
        address varchar(7) PRIMARY KEY
    ) ON COMMIT DROP;
    TRUNCATE heligent_nl_impacted_address;

    INSERT INTO heligent_nl_impacted_address (address)
    SELECT DISTINCT record.address
    FROM aircraft_registry_record record
    WHERE record.import_batch_id = target_batch_id
      AND record.address IS NOT NULL
    ON CONFLICT DO NOTHING;

    INSERT INTO heligent_nl_impacted_address (address)
    SELECT DISTINCT activity.address
    FROM aircraft_registry_record record
    JOIN aircraft_day activity
      ON upper(activity.registration) = upper(record.registration)
    WHERE record.import_batch_id = target_batch_id
      AND record.registration IS NOT NULL
    ON CONFLICT DO NOTHING;

    INSERT INTO heligent_nl_impacted_address (address)
    SELECT DISTINCT aircraft.address
    FROM aircraft_registry_record record
    JOIN aircraft
      ON regexp_replace(upper(aircraft.registration), '[^A-Z0-9]', '', 'g') =
         regexp_replace(upper(record.registration), '[^A-Z0-9]', '', 'g')
    WHERE record.import_batch_id = target_batch_id
      AND record.registration IS NOT NULL
    ON CONFLICT DO NOTHING;

    DELETE FROM nl_aircraft_activity_cache cached
    USING heligent_nl_impacted_address impacted
    WHERE cached.address = impacted.address;

    INSERT INTO nl_aircraft_activity_cache (
        dataset_day_id, utc_date, address, registration, type_code,
        type_description, category, observations, active_hours,
        airborne_hours, ground_active_hours, time_observed_hours,
        distinct_airports, estimated_distance_nm, identity_source_code,
        identity_status, registry_operator, registry_operator_country,
        registry_operator_region, registry_operator_source_code
    )
    SELECT
        identity.dataset_day_id,
        identity.utc_date,
        identity.address,
        identity.registration,
        identity.type_code,
        identity.type_description,
        COALESCE(identity.resolved_category::text, 'UNKNOWN'),
        identity.observation_count,
        identity.active_time_seconds / 3600.0,
        identity.airborne_time_seconds / 3600.0,
        identity.ground_active_time_seconds / 3600.0,
        identity.time_observed_seconds / 3600.0,
        identity.distinct_airports,
        identity.estimated_distance_nm,
        identity.identity_source_code,
        identity.identity_status,
        identity.registry_operator,
        identity.registry_operator_country,
        identity.registry_operator_geographic_region,
        identity.identity_source_code
    FROM aircraft_day_identity identity
    JOIN heligent_nl_impacted_address impacted ON impacted.address = identity.address
    WHERE COALESCE(identity.resolved_category::text, 'UNKNOWN') <> 'GROUND_VEHICLE'
    ON CONFLICT (dataset_day_id, address) DO UPDATE SET
        registration = EXCLUDED.registration,
        type_code = EXCLUDED.type_code,
        type_description = EXCLUDED.type_description,
        category = EXCLUDED.category,
        identity_source_code = EXCLUDED.identity_source_code,
        identity_status = EXCLUDED.identity_status,
        registry_operator = EXCLUDED.registry_operator,
        registry_operator_country = EXCLUDED.registry_operator_country,
        registry_operator_region = EXCLUDED.registry_operator_region,
        registry_operator_source_code = EXCLUDED.registry_operator_source_code,
        refreshed_at = clock_timestamp();
    ANALYZE nl_aircraft_activity_cache;
END;
$$;

SELECT heligent_refresh_nl_activity_cache();

CREATE TABLE IF NOT EXISTS nl_aircraft_area_day_cache (
    dataset_day_id bigint NOT NULL,
    utc_date date NOT NULL,
    address varchar(7) NOT NULL,
    area_kind text NOT NULL,
    area_code text NOT NULL,
    activity_region text,
    linked_airports bigint NOT NULL,
    airport_ground_observations bigint NOT NULL,
    airport_ground_hours numeric NOT NULL,
    airport_ground_active_hours numeric NOT NULL,
    arrival_candidates bigint NOT NULL,
    departure_candidates bigint NOT NULL,
    movement_candidates bigint NOT NULL,
    refreshed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (dataset_day_id, address, area_kind, area_code),
    CONSTRAINT nl_aircraft_area_day_cache_aircraft_day_fk
        FOREIGN KEY (dataset_day_id, address)
        REFERENCES aircraft_day (dataset_day_id, address) ON DELETE CASCADE,
    CONSTRAINT nl_aircraft_area_day_cache_kind_check
        CHECK (area_kind IN ('REGION', 'COUNTRY'))
);

CREATE INDEX IF NOT EXISTS nl_aircraft_area_day_cache_lookup_idx
    ON nl_aircraft_area_day_cache (area_kind, area_code, utc_date, address);

CREATE OR REPLACE FUNCTION heligent_refresh_nl_area_cache()
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    missing_dataset_day_id bigint;
BEGIN
    FOR missing_dataset_day_id IN
        SELECT dataset.id
        FROM dataset_day dataset
        WHERE EXISTS (
            SELECT 1
            FROM aircraft_airport_day presence
            WHERE presence.dataset_day_id = dataset.id
        )
          AND NOT EXISTS (
            SELECT 1
            FROM nl_aircraft_area_day_cache cached
            WHERE cached.dataset_day_id = dataset.id
        )
    LOOP
        INSERT INTO nl_aircraft_area_day_cache (
            dataset_day_id, utc_date, address, area_kind, area_code,
            activity_region, linked_airports, airport_ground_observations,
            airport_ground_hours, airport_ground_active_hours,
            arrival_candidates, departure_candidates, movement_candidates
        )
        SELECT
            presence.dataset_day_id,
            presence.utc_date,
            presence.address,
            'REGION',
            heligent_world_region(airport.iso_country),
            heligent_world_region(airport.iso_country),
            count(DISTINCT presence.airport_ident),
            sum(presence.ground_observation_count),
            sum(presence.ground_time_seconds) / 3600.0,
            sum(presence.ground_active_time_seconds) / 3600.0,
            sum(presence.arrival_count),
            sum(presence.departure_count),
            sum(presence.arrival_count + presence.departure_count)
        FROM aircraft_airport_day presence
        JOIN airport ON airport.ident = presence.airport_ident
        WHERE presence.dataset_day_id = missing_dataset_day_id
          AND heligent_world_region(airport.iso_country) IS NOT NULL
        GROUP BY
            presence.dataset_day_id, presence.utc_date, presence.address,
            heligent_world_region(airport.iso_country)
        ON CONFLICT DO NOTHING;

        INSERT INTO nl_aircraft_area_day_cache (
            dataset_day_id, utc_date, address, area_kind, area_code,
            activity_region, linked_airports, airport_ground_observations,
            airport_ground_hours, airport_ground_active_hours,
            arrival_candidates, departure_candidates, movement_candidates
        )
        SELECT
            presence.dataset_day_id,
            presence.utc_date,
            presence.address,
            'COUNTRY',
            airport.iso_country,
            heligent_world_region(airport.iso_country),
            count(DISTINCT presence.airport_ident),
            sum(presence.ground_observation_count),
            sum(presence.ground_time_seconds) / 3600.0,
            sum(presence.ground_active_time_seconds) / 3600.0,
            sum(presence.arrival_count),
            sum(presence.departure_count),
            sum(presence.arrival_count + presence.departure_count)
        FROM aircraft_airport_day presence
        JOIN airport ON airport.ident = presence.airport_ident
        WHERE presence.dataset_day_id = missing_dataset_day_id
          AND airport.iso_country IS NOT NULL
        GROUP BY
            presence.dataset_day_id, presence.utc_date, presence.address,
            airport.iso_country, heligent_world_region(airport.iso_country)
        ON CONFLICT DO NOTHING;
    END LOOP;
    ANALYZE nl_aircraft_area_day_cache;
END;
$$;

SELECT heligent_refresh_nl_area_cache();

CREATE OR REPLACE VIEW nl_aircraft_activity AS
SELECT
    activity.utc_date,
    activity.address,
    activity.registration,
    activity.type_code,
    activity.type_description,
    activity.category,
    COALESCE(op.operator, activity.registry_operator) AS operator,
    COALESCE(op.operator_country, activity.registry_operator_country)
        AS operator_country,
    COALESCE(op.operator_region, activity.registry_operator_region) AS operator_region,
    COALESCE(op.operator_source_code, activity.registry_operator_source_code)
        AS operator_source_code,
    (CASE WHEN op.operator IS NOT NULL THEN op.confidence
          WHEN activity.registry_operator IS NOT NULL THEN 1.000::numeric
     END)::numeric(4, 3) AS operator_confidence,
    CASE WHEN op.operator IS NOT NULL THEN op.evidence_kind
         WHEN activity.registry_operator IS NOT NULL
             THEN 'REGISTRY_REGISTERED_OPERATOR'::text
    END AS operator_evidence_kind,
    activity.observations,
    activity.active_hours,
    activity.airborne_hours,
    activity.ground_active_hours,
    activity.time_observed_hours,
    activity.distinct_airports,
    activity.estimated_distance_nm,
    activity.identity_source_code,
    activity.identity_status,
    activity.dataset_day_id,
    COALESCE(op.operator_region, activity.registry_operator_region)
        AS operator_home_region
FROM nl_aircraft_activity_cache activity
LEFT JOIN nl_best_aircraft_operator op ON op.address = activity.address;

CREATE OR REPLACE VIEW nl_airport_activity AS
SELECT
    activity.utc_date,
    activity.address,
    activity.registration,
    activity.type_code,
    activity.type_description,
    activity.category,
    activity.operator,
    activity.operator_country,
    activity.operator_region,
    activity.operator_source_code,
    airport.ident AS airport_ident,
    airport.iata_code,
    COALESCE(airport.iata_code, airport.ident) AS airport,
    airport.name AS airport_name,
    airport.municipality,
    airport.iso_country AS airport_country,
    airport.iso_region,
    heligent_world_region(airport.iso_country) AS airport_region,
    presence.is_primary_airport,
    presence.link_method,
    presence.presence_count,
    presence.ground_observation_count AS airport_ground_observations,
    presence.ground_time_seconds / 3600.0 AS airport_ground_hours,
    presence.ground_active_time_seconds / 3600.0 AS airport_ground_active_hours,
    presence.arrival_count AS arrival_candidates,
    presence.departure_count AS departure_candidates,
    presence.arrival_count + presence.departure_count AS movement_candidates,
    presence.inferred_endpoint_count,
    activity.observations AS aircraft_day_observations,
    activity.active_hours AS aircraft_day_active_hours,
    activity.airborne_hours AS aircraft_day_airborne_hours,
    activity.operator_home_region,
    heligent_world_region(airport.iso_country) AS activity_region,
    airport.iso_country AS activity_country
FROM nl_aircraft_activity activity
JOIN aircraft_airport_day presence
  ON presence.dataset_day_id = activity.dataset_day_id
 AND presence.address = activity.address
JOIN airport ON airport.ident = presence.airport_ident;

CREATE OR REPLACE VIEW nl_region_activity AS
SELECT
    activity.utc_date,
    activity.address,
    activity.registration,
    activity.type_code,
    activity.type_description,
    activity.category,
    activity.operator,
    activity.operator_country,
    activity.operator_home_region,
    activity.operator_source_code,
    area.area_code AS activity_region,
    area.linked_airports,
    area.airport_ground_observations,
    area.airport_ground_hours,
    area.airport_ground_active_hours,
    area.arrival_candidates,
    area.departure_candidates,
    area.movement_candidates,
    activity.observations AS aircraft_day_observations,
    activity.active_hours AS aircraft_day_active_hours,
    activity.airborne_hours AS aircraft_day_airborne_hours
FROM nl_aircraft_activity activity
JOIN nl_aircraft_area_day_cache area
  ON area.dataset_day_id = activity.dataset_day_id
 AND area.address = activity.address
 AND area.area_kind = 'REGION';

CREATE OR REPLACE VIEW nl_country_activity AS
SELECT
    activity.utc_date,
    activity.address,
    activity.registration,
    activity.type_code,
    activity.type_description,
    activity.category,
    activity.operator,
    activity.operator_country,
    activity.operator_home_region,
    activity.operator_source_code,
    area.area_code AS activity_country,
    area.activity_region,
    area.linked_airports,
    area.airport_ground_observations,
    area.airport_ground_hours,
    area.airport_ground_active_hours,
    area.arrival_candidates,
    area.departure_candidates,
    area.movement_candidates,
    activity.observations AS aircraft_day_observations,
    activity.active_hours AS aircraft_day_active_hours,
    activity.airborne_hours AS aircraft_day_airborne_hours
FROM nl_aircraft_activity activity
JOIN nl_aircraft_area_day_cache area
  ON area.dataset_day_id = activity.dataset_day_id
 AND area.address = activity.address
 AND area.area_kind = 'COUNTRY';

CREATE OR REPLACE VIEW nl_company AS
SELECT
    directory.name AS company,
    directory.legal_name,
    directory.trading_name,
    directory.is_operator,
    directory.is_mro,
    directory.country_code::text AS country,
    directory.geographic_region,
    directory.website,
    directory.site_count,
    directory.valid_approval_count,
    directory.capability_count,
    directory.assigned_aircraft_count
FROM company_directory directory
WHERE directory.active;

CREATE OR REPLACE VIEW nl_company_site AS
SELECT
    c.name AS company,
    s.name AS site,
    s.airport_ident,
    s.locality,
    s.region,
    s.postal_code,
    s.country_code::text AS country,
    s.geographic_region,
    s.is_primary,
    s.is_base_maintenance,
    s.is_line_maintenance
FROM company_site s
JOIN company c ON c.id = s.company_id
WHERE s.active AND c.active;

CREATE OR REPLACE VIEW nl_approval AS
SELECT
    c.name AS company,
    c.country_code::text AS company_country,
    c.geographic_region,
    approval.authority_code,
    approval.approval_type,
    approval.approval_number,
    approval.status,
    approval.valid_from,
    approval.valid_to,
    approval.last_verified_at,
    approval.source_url
FROM regulatory_approval approval
JOIN company c ON c.id = approval.company_id
WHERE c.active;

CREATE OR REPLACE VIEW nl_capability AS
SELECT
    c.name AS company,
    c.country_code::text AS company_country,
    c.geographic_region,
    approval.approval_number,
    approval.authority_code,
    approval.status AS approval_status,
    capability.capability_kind,
    capability.rating_class,
    capability.rating_code,
    capability.manufacturer,
    capability.model,
    capability.aircraft_type_code,
    capability.limitation AS capability,
    site.name AS site,
    site.locality,
    site.country_code::text AS site_country,
    capability.is_base_maintenance,
    capability.is_line_maintenance
FROM approval_capability capability
JOIN regulatory_approval approval
  ON approval.id = capability.regulatory_approval_id
JOIN company c ON c.id = approval.company_id
LEFT JOIN company_site site ON site.id = capability.company_site_id
WHERE capability.active AND c.active;

CREATE OR REPLACE VIEW nl_aircraft_assignment AS
SELECT
    claim.operator,
    claim.operator_country,
    claim.operator_region,
    COALESCE(claim.aircraft_address, claim.reported_aircraft_address) AS address,
    claim.registration,
    claim.operator_source_code AS source_code,
    claim.confidence,
    claim.evidence_kind
FROM nl_operator_claim claim;

COMMIT;
