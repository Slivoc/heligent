BEGIN;
CREATE TABLE IF NOT EXISTS capability_aircraft_mapping (
    id bigserial PRIMARY KEY,
    capability_id bigint NOT NULL REFERENCES approval_capability(id) ON DELETE RESTRICT,
    aircraft_type_code text NOT NULL CHECK (aircraft_type_code ~ '^[A-Z0-9]{2,4}$'),
    match_level text NOT NULL CHECK (match_level IN ('POSSIBLE_FAMILY','REVIEWED_TYPE')),
    variant_scope text NOT NULL DEFAULT '',
    source_url text NOT NULL,
    notes text NOT NULL,
    evidence_snapshot jsonb NOT NULL,
    active boolean NOT NULL DEFAULT true,
    revision integer NOT NULL DEFAULT 1,
    reviewed_by text NOT NULL,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(capability_id, aircraft_type_code)
);
CREATE TABLE IF NOT EXISTS capability_mapping_revision (
    id bigserial PRIMARY KEY,
    mapping_id bigint NOT NULL REFERENCES capability_aircraft_mapping(id) ON DELETE RESTRICT,
    snapshot jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
