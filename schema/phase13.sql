-- Phase 13: persist the raw transport selected for every ingestion request.

BEGIN;

ALTER TABLE ingestion_queue
    ADD COLUMN IF NOT EXISTS raw_source text NOT NULL DEFAULT 'DIRECT';

DO $$
BEGIN
    ALTER TABLE ingestion_queue
        ADD CONSTRAINT ingestion_queue_raw_source_check
        CHECK (raw_source IN ('DIRECT', 'PI'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

COMMIT;
