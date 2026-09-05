BEGIN;
CREATE TABLE IF NOT EXISTS maintenance_watchlist (
    id bigserial PRIMARY KEY,
    name text NOT NULL,
    scope_key text NOT NULL UNIQUE
);
INSERT INTO maintenance_watchlist(name, scope_key) VALUES ('Heligent team', 'internal')
ON CONFLICT (scope_key) DO NOTHING;
CREATE TABLE IF NOT EXISTS maintenance_watch (
    id bigserial PRIMARY KEY,
    watchlist_id bigint NOT NULL REFERENCES maintenance_watchlist(id),
    registration text NOT NULL,
    notes text NOT NULL DEFAULT '',
    active boolean NOT NULL DEFAULT true,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(watchlist_id, registration)
);
CREATE TABLE IF NOT EXISTS maintenance_event (
    id bigserial PRIMARY KEY,
    watch_id bigint NOT NULL REFERENCES maintenance_watch(id),
    candidate_key text,
    started_at timestamptz NOT NULL,
    ended_at timestamptz NOT NULL,
    maintenance_kind text NOT NULL,
    status text NOT NULL CHECK(status IN ('CONFIRMED','REJECTED','UNCERTAIN')),
    notes text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}',
    reviewed_by text NOT NULL,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    CHECK(ended_at >= started_at),
    UNIQUE(watch_id, candidate_key)
);
CREATE TABLE IF NOT EXISTS maintenance_event_revision (
    id bigserial PRIMARY KEY,
    event_id bigint NOT NULL REFERENCES maintenance_event(id),
    snapshot jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
