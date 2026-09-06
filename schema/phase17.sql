-- Compact observed airborne paths. NULL means legacy/not retained, not no flight.
BEGIN;
ALTER TABLE aircraft_flight_segment ADD COLUMN IF NOT EXISTS track jsonb;
CREATE INDEX IF NOT EXISTS aircraft_day_map_registration_idx
    ON aircraft_day (replace(upper(registration), '-', ''), utc_date);
COMMIT;
