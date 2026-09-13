BEGIN;
ALTER TABLE aircraft_metadata_override
    ADD COLUMN IF NOT EXISTS fill_missing_only boolean NOT NULL DEFAULT false;

CREATE OR REPLACE FUNCTION apply_aircraft_metadata_override()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE correction aircraft_metadata_override%ROWTYPE;
BEGIN
    SELECT * INTO correction FROM aircraft_metadata_override
    WHERE address=NEW.address AND NEW.utc_date>=valid_from
      AND (valid_to IS NULL OR NEW.utc_date<=valid_to)
    ORDER BY valid_from DESC LIMIT 1;
    IF NOT FOUND THEN RETURN NEW; END IF;
    IF correction.fill_missing_only THEN
        -- Provisional community fills must yield to reported identities on reingest.
        IF (nullif(btrim(NEW.registration),'') IS NOT NULL AND
            replace(upper(btrim(NEW.registration)),'-','') IS DISTINCT FROM
            replace(upper(correction.registration),'-','')) OR
           (nullif(btrim(NEW.type_code),'') IS NOT NULL AND correction.type_code IS NOT NULL
            AND upper(btrim(NEW.type_code))<>correction.type_code) THEN
            RETURN NEW;
        END IF;
        NEW.registration := coalesce(nullif(btrim(NEW.registration),''),correction.registration);
        NEW.type_code := coalesce(nullif(btrim(NEW.type_code),''),correction.type_code);
        NEW.type_description := coalesce(nullif(btrim(NEW.type_description),''),correction.type_description);
        UPDATE aircraft SET
            registration=coalesce(nullif(btrim(registration),''),correction.registration),
            type_code=coalesce(nullif(btrim(type_code),''),correction.type_code),
            type_description=coalesce(nullif(btrim(type_description),''),correction.type_description),
            updated_at=clock_timestamp()
        WHERE address=NEW.address
          AND last_seen_date BETWEEN correction.valid_from AND correction.valid_to
          AND (nullif(btrim(registration),'') IS NULL OR
               replace(upper(btrim(registration)),'-','')=replace(upper(correction.registration),'-',''))
          AND (nullif(btrim(type_code),'') IS NULL OR correction.type_code IS NULL OR upper(btrim(type_code))=correction.type_code);
    ELSE
        NEW.registration := coalesce(correction.registration,NEW.registration);
        NEW.type_code := coalesce(correction.type_code,NEW.type_code);
        NEW.type_description := coalesce(correction.type_description,NEW.type_description);
        UPDATE aircraft SET registration=coalesce(correction.registration,registration),
            type_code=coalesce(correction.type_code,type_code),
            type_description=coalesce(correction.type_description,type_description),updated_at=clock_timestamp()
        WHERE address=NEW.address;
    END IF;
    RETURN NEW;
END;
$$;
COMMIT;
