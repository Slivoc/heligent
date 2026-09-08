BEGIN;
CREATE TABLE IF NOT EXISTS aircraft_identity_review (
    id bigserial PRIMARY KEY,
    address varchar(7) NOT NULL,
    assignment jsonb NOT NULL,
    previous_rows jsonb NOT NULL,
    previous_aircraft jsonb NOT NULL,
    reviewed_by text NOT NULL,
    reviewed_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
