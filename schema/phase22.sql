BEGIN;
CREATE TABLE IF NOT EXISTS tool_source_settings (
    source_code text PRIMARY KEY,
    refresh_days integer NOT NULL CHECK (refresh_days BETWEEN 1 AND 3650),
    notes text NOT NULL DEFAULT '',
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- Individual external lookups are review evidence, never authoritative assignments.
CREATE TABLE IF NOT EXISTS tool_source_lookup (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_code text NOT NULL,
    address char(6) NOT NULL CHECK (address ~ '^[0-9a-f]{6}$'),
    status text NOT NULL CHECK (status IN ('FOUND', 'NOT_FOUND', 'FAILED')),
    fetched_at timestamptz NOT NULL DEFAULT now(),
    source_url text NOT NULL,
    result jsonb NOT NULL,
    requested_by text NOT NULL
);
CREATE INDEX IF NOT EXISTS tool_source_lookup_cache_idx
    ON tool_source_lookup(source_code, address, fetched_at DESC);
CREATE INDEX IF NOT EXISTS tool_source_lookup_history_idx
    ON tool_source_lookup(source_code, fetched_at DESC);
COMMIT;
