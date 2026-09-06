-- Stops map: lookup acceleration only; no new trace/coordinate retention.
BEGIN;
CREATE INDEX IF NOT EXISTS aircraft_day_map_registration_idx
    ON aircraft_day (replace(upper(registration), '-', ''), utc_date);
COMMIT;
