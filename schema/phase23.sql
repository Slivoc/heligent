BEGIN;
-- Community reference files remain separate from authoritative registry identities.
CREATE TABLE IF NOT EXISTS tar1090_snapshot (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fingerprint text NOT NULL UNIQUE,
    source_date timestamptz NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL,
    aircraft_gzip bytea NOT NULL,
    types_gzip bytea NOT NULL
);
CREATE TABLE IF NOT EXISTS tar1090_attempt (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL CHECK (status IN ('RUNNING','IMPORTED','UNCHANGED','FAILED')),
    snapshot_id bigint REFERENCES tar1090_snapshot(id),
    error_message text,
    requested_by text NOT NULL
);
CREATE TABLE IF NOT EXISTS tar1090_preview (
    id uuid PRIMARY KEY,
    snapshot_id bigint NOT NULL REFERENCES tar1090_snapshot(id),
    report jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
