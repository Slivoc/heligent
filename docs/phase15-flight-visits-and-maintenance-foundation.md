# Phase 15: flight, visit, and Maintenance Pulse foundation

## What changed

Reprocessing a raw ADSB.lol day now retains compact, evidence-bearing episodes
in addition to the existing daily aggregates:

- `aircraft_flight_segment`: one inferred airborne episode, with first/last
  airborne observations, estimated takeoff/landing boundaries, known
  origin/destination airports, callsigns, coverage gaps, and confidence;
- `aircraft_airport_visit`: every distinct airport contact during the day,
  including repeat visits to the same airport, arrival/departure evidence,
  direct-ground and low/slow proximity counts, open day boundaries, and
  confidence;
- `aircraft_airport_day`: remains the compatibility layer for the existing UI
  and API, but now aggregates all visits rather than assuming at most one visit
  and one movement per airport/day.

No individual ADS-B positions are inserted into PostgreSQL. Positions still
exist only while one gzip aircraft trace is being processed. This keeps the
derived store far smaller than a permanent position table while allowing the
Pi archive to be the durable source for future parser versions. The seven-day
pilot below measures the real VPS storage cost before a long backfill.

Every rebuilt `dataset_day` records `derivation_version = flight-visits-v1`
and the complete threshold configuration as JSON. Legacy dates have no version,
which makes it easy to find exactly which days still need rebuilding.

## Evidence model

Airport evidence is ranked rather than presented as fact:

1. Literal `ground` observations inside an airport radius are strongest.
2. Internal low/slow contacts require no more than 500 feet above airport
   elevation and no more than 100 knots.
3. Only the first and last positioned observations may use the broader legacy
   endpoint threshold of 1,500 feet and 200 knots.
4. An arrival or departure requires the track to cross the airport's larger
   hysteresis radius. Overlapping airport/heliport footprints retain their
   current airport until that exit occurs.

Flight time is deliberately exposed in two forms:

- `observed_airborne_seconds` sums only consecutive airborne observations no
  more than 120 seconds apart. It is conservative and can undercount poor
  coverage.
- `elapsed_airborne_seconds` spans the inferred takeoff-to-landing boundaries.
  It is more useful for comparison but includes `unobserved_seconds`, which
  must be considered before treating it as a utilisation estimate.

Use `confidence`, `starts_before_window`, `ends_after_window`, and
`quality_flags` when selecting episodes. These remain observational estimates,
not flight logs, tech logs, or certified maintenance records.

## Maintenance Pulse foundation

`nl_mro_stay_candidate` pairs an arrival that remains at a known MRO-linked
airport at the end of its daily trace with the next trace-start departure from
the same airport. It requires at least 36 elapsed hours and reports:

- the possible maintenance window and MRO companies linked to the airport;
- processed-day coverage across the window;
- intervening flight and active time;
- boundary evidence and a LOW/MEDIUM/HIGH confidence grade.

`nl_maintenance_interval_candidate` adds the calendar days, flight count,
observed airborne hours, and elapsed airborne hours since the previous
candidate for the same tail. This is the raw benchmark needed to learn a
tail-specific maintenance rhythm before the next likely event.

These views intentionally say **candidate**. A long quiet stay at an MRO airport
can be maintenance, parking, a weekend, or missing coverage. The next product
step should add analyst confirmation/rejection and use only validated repeated
events for an actionable due-soon forecast.

Candidate coverage counts only days rebuilt by an episode-capable parser;
legacy daily-summary rows do not make a quiet interval appear complete.

Example inspection:

```sql
SELECT
    registration,
    airport,
    candidate_started_at,
    candidate_ended_at,
    elapsed_days,
    coverage_ratio,
    intermediate_flight_hours,
    confidence
FROM nl_mro_stay_candidate
WHERE category = 'ROTORCRAFT'
ORDER BY candidate_started_at DESC
LIMIT 100;
```

```sql
SELECT
    registration,
    candidate_started_at,
    interval_days,
    interval_flights,
    interval_elapsed_airborne_hours,
    interval_observed_airborne_hours,
    confidence
FROM nl_maintenance_interval_candidate
WHERE category = 'ROTORCRAFT'
ORDER BY registration, candidate_started_at;
```

An MRO must be active, have `company.is_mro = true`, and have at least one
active `company_site` linked to an `airport_ident` before it can appear.

## Recommended historical rebuild

Deploy the parser and migration first. Then use **Data control**:

1. select **Raspberry Pi archive API**;
2. choose a range of no more than 31 days;
3. enable **Reprocess completed dates in this range**;
4. leave **Keep raw files after processing** off on the VPS;
5. submit each older block after the previous block completes.

The worker remains sequential. Each day is re-downloaded from the Pi, parsed in
the background, and replaced in one transaction. Existing rows remain readable
until the replacement commits; a failed parse rolls back. The transferred VPS
copy is deleted after success, while the Pi's archive copy is untouched.

Start with seven recent complete days and inspect counts, confidence, processing
time, and database growth. Then rebuild the most recent 90 days; that is enough
to evaluate visit quality and find short MRO stays. If those diagnostics look
sensible, rebuild six to twelve months for recurring maintenance intervals.
Work newest to oldest so the operational views improve first, and run backfills
off-peak because the tighter internal airport matching adds CPU work.

## Rebuild checks

The Data control screen shows flight-episode and airport-visit counts for each
new run. Database checks for a range are:

```sql
SELECT
    utc_date,
    source_aircraft_count,
    flight_segment_record_count,
    airport_visit_record_count,
    derivation_version,
    status
FROM dataset_day
WHERE utc_date BETWEEN DATE '2026-06-01' AND DATE '2026-08-31'
ORDER BY utc_date;
```

```sql
SELECT
    confidence,
    count(*) AS flights,
    round(sum(observed_airborne_seconds) / 3600.0, 1) AS observed_hours,
    round(sum(elapsed_airborne_seconds) / 3600.0, 1) AS elapsed_hours,
    round(sum(unobserved_seconds) / 3600.0, 1) AS unobserved_hours
FROM aircraft_flight_segment
WHERE utc_date BETWEEN DATE '2026-06-01' AND DATE '2026-08-31'
GROUP BY confidence
ORDER BY confidence;
```

```sql
SELECT
    airport_ident,
    count(*) AS visits,
    count(*) FILTER (WHERE arrived_at IS NOT NULL) AS arrivals,
    count(*) FILTER (WHERE departed_at IS NOT NULL) AS departures,
    count(*) FILTER (WHERE ground_observation_count > 0) AS with_ground_evidence
FROM aircraft_airport_visit
WHERE utc_date BETWEEN DATE '2026-06-01' AND DATE '2026-08-31'
GROUP BY airport_ident
ORDER BY visits DESC
LIMIT 100;
```

Large gaps between elapsed and observed hours are not automatically errors;
they indicate a source-coverage question. Compare that gap by tail, date,
source type, and confidence before choosing which duration should feed a
commercial metric.

Measure actual derived storage after the seven-day pilot rather than relying on
a row-size guess:

```sql
SELECT
    pg_size_pretty(pg_total_relation_size('aircraft_flight_segment'))
        AS flight_segments,
    pg_size_pretty(pg_total_relation_size('aircraft_airport_visit'))
        AS airport_visits;
```
