# Phase 2: airport hub and tail activity design

## Product interpretation

The analytical unit is now a **tail/day** and a **tail/airport/day**, not a
journey or individual position. This supports questions such as:

- Which tails were active at this hub?
- How many observational active or airborne hours did those tails accumulate?
- Which aircraft types dominate a hub, and how is that changing by day?
- Which airport appears to be each tail's primary operating location that day?

The pipeline intentionally does not try to reconstruct perfect flights,
origins/destinations, or routes.

This is the original daily-summary design. Phase 15 retains compact inferred
flight and repeat-visit episodes without storing positions; the daily tables
remain its compatibility rollups. See
[`phase15-flight-visits-and-maintenance-foundation.md`](phase15-flight-visits-and-maintenance-foundation.md).

## Airport association

The reference source is the public-domain OurAirports `airports.csv`. Closed
airports are excluded. Large, medium, small, heliport, seaplane, and balloonport
records are indexed in an in-memory 0.1-degree spatial grid.

Explicit `ground` observations remain the strongest airport evidence. To cover
aircraft—especially helicopters—whose transponders never report a ground state,
the first and last usable trace positions can also create a conservative
endpoint link. Each candidate is assigned to the best-scoring reference airport
within that airport type's radius:

| Airport type | Default radius |
|---|---:|
| Large airport | 4.0 NM |
| Medium airport | 3.0 NM |
| Small airport | 2.0 NM |
| Heliport | 1.2 NM |
| Seaplane base | 2.0 NM |
| Balloonport | 1.0 NM |

An inferred endpoint must be no more than 1,500 feet above airport elevation and
no faster than 200 knots. An arrival or departure is counted only when the same
daily trace also travels beyond a hysteresis zone at least 1 NM and 1.5 times
the airport match radius away. This rejects ordinary nearby overflights while
capturing low/slow traces which begin or end at a hub. The thresholds and radii
are configuration, not buried aircraft type lists.

Candidate scoring gives a modest preference to large/medium airports, scheduled
service, and the previously matched airport. This prevents a helipad physically
inside a major-airport footprint from absorbing the airport's airline traffic,
without preventing standalone heliports from matching.

## Hours and continuity

The source provides observations rather than engine or block time. The pipeline
therefore labels all hours as estimates and accumulates only consecutive
intervals up to 120 seconds. Longer gaps add no time.

In these metrics a tail is a unique ADS-B address for the UTC day, with
registration retained when the source supplies it. This is deliberately more
stable than grouping on nullable registration metadata, but is not a guarantee
of permanent airframe identity.

This protects activity totals from an aircraft observed briefly in the morning
and again in the evening. It also means poor coverage will undercount activity,
which is preferable to manufacturing hours.

## Storage and query shape

No observation or journey table is created. A complete date transaction writes:

- one `aircraft_day` row per source trace;
- zero or more daily `aircraft_airport_day` links with ground and/or inferred
  endpoint evidence plus aggregate arrival/departure candidate counts;
- an up-to-date convenience `aircraft` identity row;
- exact source/derived counts and timings on `dataset_day`.

The type, hub/day, and hub/type/day views are constrained, read-only targets
suitable for a later natural-language analytics layer. Whole-day tail activity
and airborne hours are attributed only to each tail's primary observed airport,
so a tail linked to multiple hubs is not double-counted in hub totals.

## Known limitations

- Endpoint movements are conservative candidates, not certified arrivals or
  departures. Coverage cut-offs near an airport can still create ambiguity.
- Airport presence depends on source position quality and airport reference
  coordinate quality.
- Dense urban airport/heliport clusters may still produce ambiguous nearest
  matches.
- Primary airport is a day-level observation heuristic, not a declared base.
- Active and airborne hours are ADS-B coverage estimates, not commercial,
  maintenance, or regulatory time.
- Non-ICAO `~xxxxxx` addresses can be transient and must not be treated as stable
  airframes across long periods without enrichment.

These limitations are stored or documented explicitly so the prototype can be
used to learn which hub metrics justify more sophisticated derivation later.
