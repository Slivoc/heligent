-- Phase 8 migration: provenance-backed operator and maintenance organisation data.
-- PostgreSQL 15+; safe to apply repeatedly after phase2.sql.

BEGIN;

CREATE OR REPLACE FUNCTION heligent_world_region(country_value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
RETURNS NULL ON NULL INPUT
AS $$
    SELECT CASE
        WHEN upper(btrim(country_value)) = ANY (ARRAY[
            'AL','AD','AT','BY','BE','BA','BG','HR','CZ','DK','EE','FI','FR','DE',
            'GR','VA','HU','IS','IE','IT','LV','LI','LT','LU','MT','MD','MC','ME',
            'NL','MK','NO','PL','PT','RO','RU','SM','RS','SK','SI','ES','SE','CH',
            'UA','GB','XK','EUROPE','UNITED KINGDOM'
        ]) THEN 'EUROPE'
        WHEN upper(btrim(country_value)) = ANY (ARRAY[
            'AG','BS','BB','BZ','CA','CR','CU','DM','DO','SV','GD','GT','HT','HN',
            'JM','MX','NI','PA','KN','LC','VC','TT','US','NORTH AMERICA',
            'UNITED STATES','UNITED STATES OF AMERICA'
        ]) THEN 'NORTH_AMERICA'
        WHEN upper(btrim(country_value)) = ANY (ARRAY[
            'AR','BO','BR','CL','CO','EC','GY','PY','PE','SR','UY','VE','GF','FK',
            'SOUTH AMERICA'
        ]) THEN 'SOUTH_AMERICA'
        WHEN upper(btrim(country_value)) = ANY (ARRAY[
            'DZ','AO','BJ','BW','BF','BI','CV','CM','CF','TD','KM','CG','CD','CI',
            'DJ','EG','GQ','ER','SZ','ET','GA','GM','GH','GN','GW','KE','LS','LR',
            'LY','MG','MW','ML','MR','MU','MA','MZ','NA','NE','NG','RW','ST','SN',
            'SC','SL','SO','ZA','SS','SD','TZ','TG','TN','UG','ZM','ZW','EH','AFRICA'
        ]) THEN 'AFRICA'
        WHEN upper(btrim(country_value)) = ANY (ARRAY[
            'AF','AM','AZ','BH','BD','BT','BN','KH','CN','CY','GE','HK','IN','ID',
            'IR','IQ','IL','JP','JO','KZ','KP','KR','KW','KG','LA','LB','MO','MY',
            'MV','MN','MM','NP','OM','PK','PS','PH','QA','SA','SG','LK','SY','TW',
            'TJ','TH','TL','TM','TR','AE','UZ','VN','YE','ASIA','HONG KONG',
            'UNITED ARAB EMIRATES'
        ]) THEN 'ASIA'
        WHEN upper(btrim(country_value)) = ANY (ARRAY[
            'AU','FJ','KI','MH','FM','NR','NZ','PW','PG','WS','SB','TO','TV','VU',
            'NC','PF','GU','AS','MP','CK','NU','TK','WF','OCEANIA','AUSTRALIA','FIJI'
        ]) THEN 'OCEANIA'
        WHEN upper(btrim(country_value)) = ANY (ARRAY['AQ','ANTARCTICA'])
            THEN 'ANTARCTICA'
        ELSE NULL
    END
$$;

CREATE OR REPLACE FUNCTION heligent_registration_region(registration_value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
RETURNS NULL ON NULL INPUT
AS $$
    SELECT CASE
        WHEN upper(btrim(registration_value)) ~
             '^(EC|G|I|D|F|LN|OE|SE|OY|HB|SP|OM|LX|OK|PH|EI|9H|CS|OO|M)-|^(AS-|CSX[0-9]|MT-|XV[0-9])'
            THEN 'EUROPE'
        WHEN upper(btrim(registration_value)) ~
             '^(PR|PS|PP|CP|CX)-'
            THEN 'SOUTH_AMERICA'
        WHEN upper(btrim(registration_value)) ~
             '^(N[0-9]|C-|9Y-|AW-|VP-C)'
            THEN 'NORTH_AMERICA'
        WHEN upper(btrim(registration_value)) ~
             '^(5N|SU|V5|6V|9G|CN|TS|ZS|S7|TU|C9|ZT)-'
            THEN 'AFRICA'
        WHEN upper(btrim(registration_value)) ~
             '^(A7|VT|9M|RP|4X|TC|A4O|HL|PK|XV)-|^HL[0-9]'
            THEN 'ASIA'
        WHEN upper(btrim(registration_value)) ~
             '^(VH|P2|ZK)-'
            THEN 'OCEANIA'
        ELSE NULL
    END
$$;

CREATE TABLE IF NOT EXISTS company_data_source (
    code text PRIMARY KEY,
    name text NOT NULL,
    source_kind text NOT NULL DEFAULT 'OTHER',
    authority_code text,
    source_url text,
    data_license text,
    notes text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT company_data_source_code_shape CHECK (
        code ~ '^[A-Z0-9][A-Z0-9_.-]{1,63}$'
    ),
    CONSTRAINT company_data_source_kind_check CHECK (
        source_kind IN ('REGULATOR', 'REGISTRY', 'CURATED', 'COMMERCIAL', 'OTHER')
    )
);

CREATE TABLE IF NOT EXISTS company_import_batch (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_code text NOT NULL REFERENCES company_data_source (code),
    file_name text NOT NULL,
    sha256 char(64) NOT NULL,
    status text NOT NULL DEFAULT 'RUNNING',
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    input_rows integer,
    imported_rows integer,
    company_count integer,
    site_count integer,
    approval_count integer,
    capability_count integer,
    aircraft_assignment_count integer,
    error_message text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT company_import_batch_status_check CHECK (
        status IN ('RUNNING', 'SUCCEEDED', 'FAILED')
    ),
    CONSTRAINT company_import_batch_counts_nonnegative CHECK (
        (input_rows IS NULL OR input_rows >= 0)
        AND (imported_rows IS NULL OR imported_rows >= 0)
        AND (company_count IS NULL OR company_count >= 0)
        AND (site_count IS NULL OR site_count >= 0)
        AND (approval_count IS NULL OR approval_count >= 0)
        AND (capability_count IS NULL OR capability_count >= 0)
        AND (aircraft_assignment_count IS NULL OR aircraft_assignment_count >= 0)
    ),
    CONSTRAINT company_import_batch_metadata_object CHECK (
        jsonb_typeof(metadata) = 'object'
    )
);

CREATE INDEX IF NOT EXISTS company_import_batch_source_date_idx
    ON company_import_batch (source_code, started_at DESC);

CREATE TABLE IF NOT EXISTS company (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_key text NOT NULL UNIQUE,
    name text NOT NULL,
    legal_name text,
    trading_name text,
    is_operator boolean NOT NULL DEFAULT false,
    is_mro boolean NOT NULL DEFAULT false,
    country_code char(2),
    geographic_region text,
    website text,
    active boolean NOT NULL DEFAULT true,
    notes text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT company_key_shape CHECK (
        company_key ~ '^[a-z0-9][a-z0-9._-]{1,127}$'
    ),
    CONSTRAINT company_name_present CHECK (btrim(name) <> ''),
    CONSTRAINT company_country_code_shape CHECK (
        country_code IS NULL OR country_code ~ '^[A-Z]{2}$'
    )
);

ALTER TABLE company ADD COLUMN IF NOT EXISTS geographic_region text;
UPDATE company
SET geographic_region = heligent_world_region(country_code)
WHERE geographic_region IS DISTINCT FROM heligent_world_region(country_code);

CREATE INDEX IF NOT EXISTS company_name_search_idx ON company (lower(name));
CREATE INDEX IF NOT EXISTS company_operator_idx
    ON company (name) WHERE is_operator AND active;
CREATE INDEX IF NOT EXISTS company_mro_idx
    ON company (name) WHERE is_mro AND active;
CREATE INDEX IF NOT EXISTS company_geographic_region_idx
    ON company (geographic_region, id) WHERE active;

CREATE TABLE IF NOT EXISTS company_alias (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    alias text NOT NULL,
    normalized_alias text NOT NULL,
    alias_type text NOT NULL DEFAULT 'OTHER',
    source_code text REFERENCES company_data_source (code),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT company_alias_present CHECK (
        btrim(alias) <> '' AND btrim(normalized_alias) <> ''
    ),
    CONSTRAINT company_alias_type_check CHECK (
        alias_type IN ('LEGAL', 'TRADING_AS', 'FORMER', 'SOURCE', 'OTHER')
    ),
    CONSTRAINT company_alias_unique UNIQUE (company_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS company_alias_search_idx
    ON company_alias (normalized_alias);

CREATE TABLE IF NOT EXISTS company_external_identifier (
    source_code text NOT NULL REFERENCES company_data_source (code),
    external_id text NOT NULL,
    company_id bigint NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    source_updated_at timestamptz,
    last_seen_batch_id bigint REFERENCES company_import_batch (id),
    active boolean NOT NULL DEFAULT true,
    raw_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    imported_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (source_code, external_id),
    CONSTRAINT company_external_id_present CHECK (btrim(external_id) <> ''),
    CONSTRAINT company_external_raw_object CHECK (jsonb_typeof(raw_data) = 'object')
);

CREATE INDEX IF NOT EXISTS company_external_identifier_company_idx
    ON company_external_identifier (company_id);

CREATE TABLE IF NOT EXISTS company_site (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    site_key text NOT NULL,
    name text NOT NULL,
    airport_ident text REFERENCES airport (ident) ON DELETE SET NULL,
    address_line_1 text,
    address_line_2 text,
    locality text,
    region text,
    postal_code text,
    country_code char(2),
    geographic_region text,
    latitude_deg double precision,
    longitude_deg double precision,
    is_primary boolean NOT NULL DEFAULT false,
    is_base_maintenance boolean NOT NULL DEFAULT false,
    is_line_maintenance boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT company_site_key_present CHECK (btrim(site_key) <> ''),
    CONSTRAINT company_site_name_present CHECK (btrim(name) <> ''),
    CONSTRAINT company_site_country_code_shape CHECK (
        country_code IS NULL OR country_code ~ '^[A-Z]{2}$'
    ),
    CONSTRAINT company_site_coordinates_pair CHECK (
        (latitude_deg IS NULL AND longitude_deg IS NULL)
        OR (
            latitude_deg BETWEEN -90 AND 90
            AND longitude_deg BETWEEN -180 AND 180
        )
    ),
    CONSTRAINT company_site_key_unique UNIQUE (company_id, site_key)
);

ALTER TABLE company_site ADD COLUMN IF NOT EXISTS geographic_region text;
UPDATE company_site
SET geographic_region = heligent_world_region(country_code)
WHERE geographic_region IS DISTINCT FROM heligent_world_region(country_code);

CREATE INDEX IF NOT EXISTS company_site_airport_idx
    ON company_site (airport_ident) WHERE airport_ident IS NOT NULL AND active;
CREATE INDEX IF NOT EXISTS company_site_country_idx
    ON company_site (country_code, company_id) WHERE active;
CREATE INDEX IF NOT EXISTS company_site_geographic_region_idx
    ON company_site (geographic_region, company_id) WHERE active;

CREATE TABLE IF NOT EXISTS company_site_external_identifier (
    source_code text NOT NULL REFERENCES company_data_source (code),
    external_id text NOT NULL,
    company_site_id bigint NOT NULL REFERENCES company_site (id) ON DELETE CASCADE,
    last_seen_batch_id bigint REFERENCES company_import_batch (id),
    active boolean NOT NULL DEFAULT true,
    raw_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    imported_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (source_code, external_id),
    CONSTRAINT company_site_external_id_present CHECK (btrim(external_id) <> ''),
    CONSTRAINT company_site_external_raw_object CHECK (jsonb_typeof(raw_data) = 'object')
);

CREATE TABLE IF NOT EXISTS regulatory_approval (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    authority_code text NOT NULL,
    approval_type text NOT NULL,
    approval_number text NOT NULL,
    status text NOT NULL DEFAULT 'VALID',
    valid_from date,
    valid_to date,
    source_url text,
    last_verified_at timestamptz,
    notes text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT regulatory_approval_fields_present CHECK (
        btrim(authority_code) <> ''
        AND btrim(approval_type) <> ''
        AND btrim(approval_number) <> ''
    ),
    CONSTRAINT regulatory_approval_status_check CHECK (
        status IN ('VALID', 'PENDING', 'SUSPENDED', 'REVOKED', 'EXPIRED', 'UNKNOWN')
    ),
    CONSTRAINT regulatory_approval_dates_order CHECK (
        valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to
    ),
    CONSTRAINT regulatory_approval_unique UNIQUE (
        authority_code, approval_type, approval_number
    )
);

CREATE INDEX IF NOT EXISTS regulatory_approval_company_idx
    ON regulatory_approval (company_id, approval_type, status);
CREATE INDEX IF NOT EXISTS regulatory_approval_lookup_idx
    ON regulatory_approval (authority_code, approval_number);

CREATE TABLE IF NOT EXISTS regulatory_approval_external_identifier (
    source_code text NOT NULL REFERENCES company_data_source (code),
    external_id text NOT NULL,
    regulatory_approval_id bigint NOT NULL
        REFERENCES regulatory_approval (id) ON DELETE CASCADE,
    last_seen_batch_id bigint REFERENCES company_import_batch (id),
    active boolean NOT NULL DEFAULT true,
    raw_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    imported_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (source_code, external_id),
    CONSTRAINT regulatory_approval_external_id_present CHECK (btrim(external_id) <> ''),
    CONSTRAINT regulatory_approval_external_raw_object CHECK (
        jsonb_typeof(raw_data) = 'object'
    )
);

CREATE TABLE IF NOT EXISTS company_site_approval (
    company_site_id bigint NOT NULL REFERENCES company_site (id) ON DELETE CASCADE,
    regulatory_approval_id bigint NOT NULL
        REFERENCES regulatory_approval (id) ON DELETE CASCADE,
    source_code text REFERENCES company_data_source (code),
    is_primary boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (company_site_id, regulatory_approval_id)
);

CREATE INDEX IF NOT EXISTS company_site_approval_approval_idx
    ON company_site_approval (regulatory_approval_id, company_site_id);

CREATE TABLE IF NOT EXISTS approval_capability (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    regulatory_approval_id bigint NOT NULL
        REFERENCES regulatory_approval (id) ON DELETE CASCADE,
    company_site_id bigint REFERENCES company_site (id) ON DELETE CASCADE,
    source_code text NOT NULL REFERENCES company_data_source (code),
    capability_key text NOT NULL,
    capability_kind text NOT NULL DEFAULT 'OTHER',
    rating_class text,
    rating_code text,
    manufacturer text,
    model text,
    aircraft_type_code text,
    limitation text,
    is_base_maintenance boolean NOT NULL DEFAULT false,
    is_line_maintenance boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true,
    last_seen_batch_id bigint REFERENCES company_import_batch (id),
    raw_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT approval_capability_key_present CHECK (btrim(capability_key) <> ''),
    CONSTRAINT approval_capability_kind_check CHECK (
        capability_kind IN (
            'AIRCRAFT', 'ENGINE', 'COMPONENT', 'SPECIALIST', 'SERVICE', 'OTHER'
        )
    ),
    CONSTRAINT approval_capability_raw_object CHECK (jsonb_typeof(raw_data) = 'object'),
    CONSTRAINT approval_capability_source_key_unique UNIQUE (
        regulatory_approval_id, source_code, capability_key
    )
);

CREATE INDEX IF NOT EXISTS approval_capability_aircraft_type_idx
    ON approval_capability (upper(aircraft_type_code))
    WHERE aircraft_type_code IS NOT NULL AND active;
CREATE INDEX IF NOT EXISTS approval_capability_model_idx
    ON approval_capability (upper(manufacturer), upper(model))
    WHERE capability_kind = 'AIRCRAFT' AND active;
CREATE INDEX IF NOT EXISTS approval_capability_site_idx
    ON approval_capability (company_site_id, capability_kind)
    WHERE company_site_id IS NOT NULL AND active;

CREATE TABLE IF NOT EXISTS company_aircraft_assignment (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    regulatory_approval_id bigint REFERENCES regulatory_approval (id) ON DELETE SET NULL,
    aircraft_address varchar(7) REFERENCES aircraft (address) ON DELETE SET NULL,
    reported_aircraft_address varchar(7),
    registration text,
    geographic_region text,
    region_basis text,
    assignment_role text NOT NULL DEFAULT 'OPERATOR',
    source_code text NOT NULL REFERENCES company_data_source (code),
    external_id text NOT NULL,
    valid_from date,
    valid_to date,
    confidence numeric(4, 3),
    active boolean NOT NULL DEFAULT true,
    last_seen_batch_id bigint REFERENCES company_import_batch (id),
    raw_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT company_aircraft_assignment_identity CHECK (
        aircraft_address IS NOT NULL
        OR reported_aircraft_address IS NOT NULL
        OR btrim(COALESCE(registration, '')) <> ''
    ),
    CONSTRAINT company_aircraft_assignment_reported_address_shape CHECK (
        reported_aircraft_address IS NULL
        OR reported_aircraft_address ~ '^(~[0-9a-f]{6}|[0-9a-f]{6})$'
    ),
    CONSTRAINT company_aircraft_assignment_role_check CHECK (
        assignment_role IN ('OPERATOR', 'OWNER', 'MANAGER', 'AOC_AUTHORIZED', 'OTHER')
    ),
    CONSTRAINT company_aircraft_assignment_dates_order CHECK (
        valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to
    ),
    CONSTRAINT company_aircraft_assignment_confidence_range CHECK (
        confidence IS NULL OR confidence BETWEEN 0 AND 1
    ),
    CONSTRAINT company_aircraft_assignment_raw_object CHECK (
        jsonb_typeof(raw_data) = 'object'
    ),
    CONSTRAINT company_aircraft_assignment_source_unique UNIQUE (source_code, external_id)
);

ALTER TABLE company_aircraft_assignment
    ADD COLUMN IF NOT EXISTS geographic_region text;
ALTER TABLE company_aircraft_assignment
    ADD COLUMN IF NOT EXISTS region_basis text;
UPDATE company_aircraft_assignment caa
SET
    geographic_region = COALESCE(
        c.geographic_region,
        heligent_registration_region(caa.registration)
    ),
    region_basis = CASE
        WHEN c.geographic_region IS NOT NULL THEN 'COMPANY_COUNTRY'
        WHEN heligent_registration_region(caa.registration) IS NOT NULL
            THEN 'REGISTRATION_PREFIX'
        ELSE NULL
    END
FROM company c
WHERE c.id = caa.company_id
  AND (
      caa.geographic_region IS DISTINCT FROM COALESCE(
          c.geographic_region,
          heligent_registration_region(caa.registration)
      )
      OR caa.region_basis IS DISTINCT FROM CASE
          WHEN c.geographic_region IS NOT NULL THEN 'COMPANY_COUNTRY'
          WHEN heligent_registration_region(caa.registration) IS NOT NULL
              THEN 'REGISTRATION_PREFIX'
          ELSE NULL
      END
  );

CREATE INDEX IF NOT EXISTS company_aircraft_assignment_registration_idx
    ON company_aircraft_assignment (upper(registration)) WHERE active;
CREATE INDEX IF NOT EXISTS company_aircraft_assignment_address_idx
    ON company_aircraft_assignment (aircraft_address) WHERE active;
CREATE INDEX IF NOT EXISTS company_aircraft_assignment_reported_address_idx
    ON company_aircraft_assignment (reported_aircraft_address) WHERE active;
CREATE INDEX IF NOT EXISTS company_aircraft_assignment_company_idx
    ON company_aircraft_assignment (company_id, assignment_role) WHERE active;
CREATE INDEX IF NOT EXISTS company_aircraft_assignment_geographic_region_idx
    ON company_aircraft_assignment (geographic_region, assignment_role) WHERE active;

CREATE OR REPLACE VIEW company_directory AS
SELECT
    -- Keep this list explicit. Later phases add columns to company, and using
    -- c.* here would shift the four aggregate columns when Phase 8 is replayed.
    c.id,
    c.company_key,
    c.name,
    c.legal_name,
    c.trading_name,
    c.is_operator,
    c.is_mro,
    c.country_code,
    c.website,
    c.active,
    c.notes,
    c.created_at,
    c.updated_at,
    (SELECT count(*) FROM company_site s WHERE s.company_id = c.id AND s.active)
        AS site_count,
    (SELECT count(*) FROM regulatory_approval a
        WHERE a.company_id = c.id AND a.status = 'VALID') AS valid_approval_count,
    (SELECT count(*) FROM approval_capability ac
        JOIN regulatory_approval a ON a.id = ac.regulatory_approval_id
        WHERE a.company_id = c.id AND ac.active) AS capability_count,
    (SELECT count(*) FROM company_aircraft_assignment caa
        WHERE caa.company_id = c.id AND caa.active) AS assigned_aircraft_count
    , c.geographic_region
FROM company c;

CREATE OR REPLACE VIEW airport_company_directory AS
SELECT
    s.airport_ident,
    s.id AS company_site_id,
    s.name AS site_name,
    s.is_primary,
    s.is_base_maintenance,
    s.is_line_maintenance,
    c.id AS company_id,
    c.company_key,
    c.name AS company_name,
    c.is_operator,
    c.is_mro
FROM company_site s
JOIN company c ON c.id = s.company_id
WHERE s.active AND c.active AND s.airport_ident IS NOT NULL;

CREATE OR REPLACE VIEW current_company_aircraft AS
SELECT
    caa.company_id,
    c.company_key,
    c.name AS company_name,
    caa.assignment_role,
    caa.aircraft_address,
    caa.reported_aircraft_address,
    caa.registration,
    caa.regulatory_approval_id,
    caa.source_code,
    caa.confidence
FROM company_aircraft_assignment caa
JOIN company c ON c.id = caa.company_id
WHERE caa.active
  AND c.active
  AND (caa.valid_from IS NULL OR caa.valid_from <= CURRENT_DATE)
  AND (caa.valid_to IS NULL OR caa.valid_to >= CURRENT_DATE);

COMMIT;
