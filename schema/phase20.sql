BEGIN;
CREATE TABLE IF NOT EXISTS tool_import_preview (
    id uuid PRIMARY KEY,
    adapter text NOT NULL,
    snapshot jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
