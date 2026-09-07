# G-WSAS — read-only VPS investigation, 7 September 2026

No production records or services were changed. PostgreSQL sessions used `default_transaction_read_only=on` and a 15-second statement timeout.

## Finding

Searching the live `aircraft` and `aircraft_day` registration fields for normalized GWSAS returns no rows. Both hex addresses 4082a1 and 4082a2 exist, but their registration and type fields are blank. There are no metadata overrides for either address.

[Flightradar's G-WSAS page](https://www.flightradar24.com/data/aircraft/g-wsas) identifies EC45 and Mode S 4082A2. Independently, Heligent has callsign GWSAS on 16 of the 17 days recorded for 4082a2. A secondary fleet listing reverses the two hex assignments, so do not copy that listing without checking identity and dates.

For **4082a2**, Heligent retains:

- 17 aircraft-day records, 14 August–5 September 2026.
- 27,496 positions and 35.27 estimated daily airborne hours.
- 133 derived flight segments, totalling 31.71 observed airborne hours. Segments are not equivalent to separate completed journeys; daily and segment estimates use different derivations.
- 75 airport-visit records across 14 airport identifiers, including 47 at EGPF. Most visits have little or no ground evidence, so visits must not be equated to confirmed landings or maintenance stops.

For comparison, 4082a1 has 36 daily records, 40,996 positions and 53.27 estimated hours, with recent callsign GXSAS. Do not combine the two aircraft.

All dates 1 August–5 September are PROCESSED with flight-visits-v1. September 6 is FAILED_DOWNLOAD, a separate coverage gap. This is not evidence of zero activity on September 6 or on days where a particular aircraft is absent.

## Explanation and proposed action

The summarizer reads registration from raw metadata `r`; it does not turn a callsign into a registration. The Stops map filters historical `aircraft_day.registration`. Blank identity therefore prevents the watched registration from finding already-derived activity and prevents type-based capability highlighting.

First verify the identity/effective date, then apply an audited, date-scoped registration/type enrichment for 4082a2 and refresh existing aircraft-day metadata as well as current aircraft metadata. Do not merely update the current aircraft row. This should recover existing derived activity without re-downloading raw files. No correction was applied during this investigation.

The exact difference from Flightradar's journey count remains unquantified: its authenticated journey list/date range was not supplied. Identity enrichment fixes the demonstrated lookup gap; it does not guarantee equivalent receiver coverage or journey segmentation.
