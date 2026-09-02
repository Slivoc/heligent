# Phase 6: airport movement candidates

Phase 6 fixes a material blind spot in hub analytics: many aircraft, especially
offshore helicopters, finish a trace at an airport without ever reporting the
literal ADS-B `ground` altitude state. Ground-only association therefore
undercounted real hub activity.

The ingestion stream now considers the first and last usable position in each
tail/day trace. A non-ground endpoint qualifies only when it is inside the
airport type's normal match radius, no more than 1,500 feet above airport
elevation, and no faster than 200 knots. It becomes a departure or arrival
candidate only if another trace position is outside a larger hysteresis zone:
the greater of radius + 1 NM or radius × 1.5.

In the original Phase 6 model PostgreSQL stored no positions or journeys.
`aircraft_airport_day` retained only:

- direct ground-observation counts and time estimates;
- inferred endpoint count and evidence method;
- at most one arrival and one departure candidate per tail/airport/day;
- the closest airport distance and evidence timestamps.

This document describes the original Phase 6 compatibility metric. Phase 15
now retains compact flight and visit episodes (still no positions), allows
multiple visits and movement candidates per tail/airport/day, and rolls them
back into `aircraft_airport_day`. See
[`phase15-flight-visits-and-maintenance-foundation.md`](phase15-flight-visits-and-maintenance-foundation.md).

Direct ground evidence outranks inferred evidence when choosing a primary
airport. Hub and hub/type rollups expose candidate movements separately from
ground observations. Natural-language answers use the same candidate wording.

The model deliberately prefers useful, explainable hub evidence over perfect
flight reconstruction. A coverage boundary near an airport can still resemble
an endpoint, so these counts must not be presented as certified airport records.
Previously processed dates need reprocessing because raw endpoint altitude and
speed were intentionally not retained in the original daily summaries.
