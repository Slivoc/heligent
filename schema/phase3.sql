-- Phase 3 migration: durable sequential queue for the data-management UI.

BEGIN;

DO $$
BEGIN
    CREATE TYPE queue_item_status AS ENUM (
        'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS ingestion_queue (
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

CREATE UNIQUE INDEX IF NOT EXISTS ingestion_queue_one_active_date_idx
    ON ingestion_queue (utc_date)
    WHERE status IN ('QUEUED', 'RUNNING');
CREATE INDEX IF NOT EXISTS ingestion_queue_claim_idx
    ON ingestion_queue (status, requested_at, id);
CREATE INDEX IF NOT EXISTS ingestion_queue_date_history_idx
    ON ingestion_queue (utc_date, id DESC);

COMMIT;
