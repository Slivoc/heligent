BEGIN;

-- Phase 4 reads by UTC range and then groups by hub. This complements the
-- Phase 2 hub-first index without changing retained data.
CREATE INDEX IF NOT EXISTS aircraft_airport_day_date_hub_idx
    ON aircraft_airport_day (utc_date, airport_ident, address);

-- Seed only high-confidence rotorcraft descriptions. The table remains the
-- explicit, editable source of truth and manual classifications always win.
INSERT INTO aircraft_type_classification (
    type_code,
    category,
    classification_source,
    confidence,
    notes
)
SELECT DISTINCT
    type_code,
    'ROTORCRAFT'::aircraft_category,
    'PHASE4_DESCRIPTION_HEURISTIC',
    0.950,
    'Conservative manufacturer/model match against ADSB.lol description metadata'
FROM aircraft
WHERE type_code IS NOT NULL
  AND type_description IS NOT NULL
  AND (
      upper(type_description) LIKE '%HELICOPTER%'
      OR upper(type_description) LIKE 'ROBINSON %'
      OR upper(type_description) LIKE 'SIKORSKY %'
      OR upper(type_description) LIKE 'BELL %'
      OR upper(type_description) LIKE 'EUROCOPTER %'
      OR upper(type_description) LIKE 'AIRBUS HELICOPTERS %'
      OR upper(type_description) LIKE 'AGUSTA %'
      OR upper(type_description) LIKE 'AGUSTAWESTLAND %'
      OR upper(type_description) LIKE 'ENSTROM %'
      OR upper(type_description) LIKE 'KAMAN %'
      OR upper(type_description) LIKE 'KAMOV %'
      OR upper(type_description) LIKE 'MIL MI-%'
      OR upper(type_description) LIKE 'MD HELICOPTERS %'
      OR upper(type_description) LIKE 'HUGHES 269%'
      OR upper(type_description) LIKE 'HUGHES 500%'
      OR upper(type_description) LIKE 'SCHWEIZER 269%'
      OR upper(type_description) LIKE 'SCHWEIZER 300%'
      OR upper(type_description) LIKE 'GUIMBAL %'
      OR upper(type_description) LIKE 'BRANTLY %'
      OR upper(type_description) LIKE 'HILLER %'
  )
ON CONFLICT (type_code) DO NOTHING;

-- High-confidence ICAO Doc 8643 rotorcraft designators used by common Airbus
-- trade/model aliases. Explicit seeds cover descriptions that predate the
-- Airbus/Eurocopter naming patterns used by the heuristic above.
INSERT INTO aircraft_type_classification (
    type_code,
    category,
    classification_source,
    confidence,
    notes
)
VALUES
    ('AS50', 'ROTORCRAFT'::aircraft_category, 'ICAO_DOC_8643', 1.000,
     'AS-350/H125 family; common model aliases resolve to ICAO designator AS50'),
    ('EC35', 'ROTORCRAFT'::aircraft_category, 'ICAO_DOC_8643', 1.000,
     'EC-135/H135 family; common model aliases resolve to ICAO designator EC35'),
    ('EC45', 'ROTORCRAFT'::aircraft_category, 'ICAO_DOC_8643', 1.000,
     'EC-145/H145 family; common model aliases resolve to ICAO designator EC45'),
    ('EC75', 'ROTORCRAFT'::aircraft_category, 'ICAO_DOC_8643', 1.000,
     'EC-175/H175/Z-15 family; common model aliases resolve to ICAO designator EC75')
ON CONFLICT (type_code) DO NOTHING;

-- Known non-aircraft emitters should not appear in tail or fleet rankings.
INSERT INTO aircraft_type_classification (
    type_code,
    category,
    classification_source,
    confidence,
    notes
)
SELECT DISTINCT
    type_code,
    'GROUND_VEHICLE'::aircraft_category,
    'PHASE4_GROUND_EMITTER_RULE',
    1.000,
    'Known tower or ground-service emitter designator'
FROM aircraft
WHERE type_code IS NOT NULL
  AND (
      upper(type_code) IN ('TWR', 'GND', 'GRND', 'SERV')
      OR upper(type_description) = 'GROUND VEHICLE'
  )
ON CONFLICT (type_code) DO NOTHING;

COMMIT;
