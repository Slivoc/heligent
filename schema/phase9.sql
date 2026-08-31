BEGIN;

ALTER TABLE company
    ADD COLUMN IF NOT EXISTS is_customer boolean NOT NULL DEFAULT false;

ALTER TABLE company_site
    ADD COLUMN IF NOT EXISTS is_of_interest boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS interest_notes text,
    ADD COLUMN IF NOT EXISTS airport_link_method text,
    ADD COLUMN IF NOT EXISTS airport_link_updated_at timestamptz;

ALTER TABLE company_site
    DROP CONSTRAINT IF EXISTS company_site_airport_link_method_check;

ALTER TABLE company_site
    ADD CONSTRAINT company_site_airport_link_method_check CHECK (
        airport_link_method IS NULL
        OR airport_link_method IN ('MANUAL', 'AUTOMATIC', 'IMPORTED')
    );

CREATE INDEX IF NOT EXISTS company_customer_idx
    ON company (name) WHERE active AND is_customer;

CREATE INDEX IF NOT EXISTS company_site_interest_idx
    ON company_site (company_id, name) WHERE active AND is_of_interest;

COMMIT;
