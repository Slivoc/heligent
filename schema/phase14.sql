-- Phase 14: externally authenticated maintenance users and audit events.

BEGIN;

CREATE TABLE IF NOT EXISTS heligent_user (
    email text PRIMARY KEY,
    display_name text,
    role text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz,
    CONSTRAINT heligent_user_role_check
        CHECK (role IN ('VIEWER', 'ANALYST', 'ADMIN')),
    CONSTRAINT heligent_user_email_check
        CHECK (email = lower(btrim(email)) AND position('@' IN email) > 1)
);

CREATE TABLE IF NOT EXISTS heligent_audit_event (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    actor_email text NOT NULL,
    actor_role text NOT NULL,
    action text NOT NULL,
    request_method text NOT NULL,
    request_path text NOT NULL,
    request_id text NOT NULL,
    remote_address inet,
    response_status integer NOT NULL,
    target text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT heligent_audit_event_role_check
        CHECK (actor_role IN ('VIEWER', 'ANALYST', 'ADMIN')),
    CONSTRAINT heligent_audit_event_method_check
        CHECK (request_method IN ('POST', 'PUT', 'PATCH', 'DELETE')),
    CONSTRAINT heligent_audit_event_status_check
        CHECK (response_status BETWEEN 100 AND 599)
);

CREATE INDEX IF NOT EXISTS heligent_audit_event_actor_idx
    ON heligent_audit_event (actor_email, occurred_at DESC);
CREATE INDEX IF NOT EXISTS heligent_audit_event_action_idx
    ON heligent_audit_event (action, occurred_at DESC);

COMMIT;
