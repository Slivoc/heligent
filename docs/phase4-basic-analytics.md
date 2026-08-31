# Phase 4: basic analytics UI

## Purpose

Phase 4 is a coverage-aware exploration surface for the compact daily data
retained in Phase 2. It deliberately prioritises:

- airport and hub traffic by unique observed tail;
- tail activity, airborne, and ground-active hour estimates;
- aircraft-type mix and observation volume;
- a separately classified rotorcraft view.

It does not retain journeys or claim certified flights or hours. Phase 6 adds
conservative aggregate arrival/departure candidates from daily trace endpoints.

## Analytics workflow

The application opens on the latest processed UTC day. The user can switch to
the latest 7 or 30 UTC days, choose a custom period, select one aircraft type,
or restrict the main results to classified rotorcraft.

Every response reports requested and locally available dates before returning
results. Partial periods remain queryable, but the interface labels them as
partial and links back to Data control to populate missing dates. No chart or
table silently treats a partial range as complete.

The page contains:

- daily unique-aircraft and observation-volume charts;
- selected-period totals for tails, observations, activity hours, and hub
  linkage;
- aircraft-type distribution and hours tables;
- busiest observed-airport rankings;
- most-active-tail rankings with strongest observed primary hub;
- a basic helicopter type and hours view;
- a visible Phase 5 natural-language analytics preview.

## API

`GET /api/analytics/snapshot` returns the complete read-only snapshot used by
the page. Optional query parameters are:

- `from=YYYY-MM-DD` and `to=YYYY-MM-DD` (both required when either is used);
- `type=H145` for an exact ICAO type-designator filter;
- `helicopters=true` to restrict the main snapshot to classified rotorcraft.

Periods are capped at 366 days. With no dates supplied, the endpoint selects
the latest locally available day.

## Metric interpretation

- Unique tails are unique ADS-B addresses, not guaranteed one-to-one legal
  airframes.
- Active and airborne hours are summed observation-derived daily estimates.
- Hub traffic uses explicit ground evidence plus conservative low/slow endpoint
  evidence near an airport.
- Arrival and departure values are analytical candidates, not certified airport
  movement records.
- A primary hub is the strongest observed airport evidence for a tail/day, not
  a declared base.
- Non-aircraft tower and ground-service emitter designators are excluded from
  fleet and tail rankings.

## Rotorcraft classification

`aircraft_type_classification` remains the explicit source of truth. The Phase
4 migration seeds only conservative manufacturer/model matches from ADSB.lol
type descriptions and never overwrites an existing classification. Unknown or
unclassified types are excluded from the helicopter view, so its totals should
be treated as a classified minimum rather than perfect fleet coverage.

The classification seed refreshes after each successful ingestion. It is
auditable and can be corrected without changing the ingestion parser.

## Local deployment boundary

Analytics and administration remain in the same loopback-only application at
`http://127.0.0.1:5080`. A hosted frontend is intentionally not published
because Data control operates a workstation-bound PostgreSQL ingestion worker.
An internet-facing version needs an authenticated, network-accessible backend.
