BEGIN;

-- Historical trace archives carry a snapshot of an external aircraft database.
-- Registrations and their allocated Mode-S addresses can be reused, so that
-- snapshot can lag a newly assigned aircraft. Keep corrections effective-dated
-- to avoid rewriting observations belonging to a previous airframe.
CREATE TABLE IF NOT EXISTS aircraft_metadata_override (
    address varchar(7) NOT NULL,
    valid_from date NOT NULL,
    valid_to date,
    registration text,
    type_code text,
    type_description text,
    source_url text NOT NULL,
    notes text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (address, valid_from),
    CONSTRAINT aircraft_metadata_override_address_lowercase CHECK (
        address = lower(address)
    ),
    CONSTRAINT aircraft_metadata_override_date_order CHECK (
        valid_to IS NULL OR valid_to >= valid_from
    )
);

INSERT INTO aircraft_metadata_override (
    address, valid_from, valid_to, registration, type_code,
    type_description, source_url, notes
)
VALUES
(
    'c012ed', DATE '2026-06-23', NULL, 'C-FHEI', 'BCS3',
    'AIRBUS A-220-300',
    'https://www.planespotters.net/airframe/airbus-a220-300-c-fhei-air-canada/38n012',
    'Delivered to Air Canada on 2026-06-23; archive metadata retained the prior AS50 identity'
),
(
    'c05ab2', DATE '2026-07-21', NULL, 'C-GIIZ', 'B38M',
    'BOEING 737 MAX 8',
    'https://www.planespotters.net/airframe/boeing-737-max-8-c-giiz-westjet/e55vzv',
    'Delivered to WestJet on 2026-07-21; archive metadata retained the prior AS50 identity'
)
ON CONFLICT (address, valid_from) DO UPDATE SET
    valid_to = EXCLUDED.valid_to,
    registration = EXCLUDED.registration,
    type_code = EXCLUDED.type_code,
    type_description = EXCLUDED.type_description,
    source_url = EXCLUDED.source_url,
    notes = EXCLUDED.notes;

CREATE OR REPLACE FUNCTION apply_aircraft_metadata_override()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    correction aircraft_metadata_override%ROWTYPE;
BEGIN
    SELECT amo.* INTO correction
    FROM aircraft_metadata_override amo
    WHERE amo.address = NEW.address
      AND NEW.utc_date >= amo.valid_from
      AND (amo.valid_to IS NULL OR NEW.utc_date <= amo.valid_to)
    ORDER BY amo.valid_from DESC
    LIMIT 1;

    IF FOUND THEN
        NEW.registration := COALESCE(correction.registration, NEW.registration);
        NEW.type_code := COALESCE(correction.type_code, NEW.type_code);
        NEW.type_description := COALESCE(
            correction.type_description, NEW.type_description
        );

        UPDATE aircraft
        SET registration = COALESCE(correction.registration, registration),
            type_code = COALESCE(correction.type_code, type_code),
            type_description = COALESCE(
                correction.type_description, type_description
            ),
            updated_at = clock_timestamp()
        WHERE address = NEW.address;
    END IF;

    RETURN NEW;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgrelid = 'aircraft_day'::regclass
          AND tgname = 'aircraft_day_metadata_override'
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER aircraft_day_metadata_override
        BEFORE INSERT OR UPDATE OF
            utc_date, address, registration, type_code, type_description
        ON aircraft_day
        FOR EACH ROW
        EXECUTE FUNCTION apply_aircraft_metadata_override();
    END IF;
END;
$$;

-- Repair rows already loaded before this migration. The trigger also updates
-- the current aircraft record and protects subsequent ingests.
UPDATE aircraft_day ad
SET registration = COALESCE(amo.registration, ad.registration),
    type_code = COALESCE(amo.type_code, ad.type_code),
    type_description = COALESCE(amo.type_description, ad.type_description)
FROM aircraft_metadata_override amo
WHERE ad.address = amo.address
  AND ad.utc_date >= amo.valid_from
  AND (amo.valid_to IS NULL OR ad.utc_date <= amo.valid_to)
  AND (ad.registration, ad.type_code, ad.type_description) IS DISTINCT FROM
      (
          COALESCE(amo.registration, ad.registration),
          COALESCE(amo.type_code, ad.type_code),
          COALESCE(amo.type_description, ad.type_description)
      );

INSERT INTO aircraft_type_classification (
    type_code, category, classification_source, confidence, notes
)
VALUES (
    'BCS3', 'FIXED_WING'::aircraft_category, 'ICAO_DOC_8643', 1.000,
    'Airbus A220-300 / Bombardier CS300'
), (
    'B38M', 'FIXED_WING'::aircraft_category, 'ICAO_DOC_8643', 1.000,
    'Boeing 737 MAX 8'
)
ON CONFLICT (type_code) DO UPDATE SET
    category = EXCLUDED.category,
    classification_source = EXCLUDED.classification_source,
    confidence = EXCLUDED.confidence,
    notes = EXCLUDED.notes,
    updated_at = clock_timestamp();

COMMIT;
