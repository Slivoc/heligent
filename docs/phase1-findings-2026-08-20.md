# Phase 1 findings: ADSB.lol 2026-08-20

Inspection run: 2026-08-22 07:25 UTC
Selected historical day: 2026-08-20 UTC

## Outcome

The source is programmatically discoverable, its split archive can be inspected
without downloading the full day, and individual aircraft traces can be parsed
with memory bounded to one aircraft file at a time. The evidence supports moving
to a one-day Phase 2 ingestion worker, but does **not** support retaining every
position as a normal PostgreSQL row in the first milestone.

The machine-readable evidence is in
[`reports/phase1-2026-08-20.json`](../reports/phase1-2026-08-20.json). The exact
proposed Phase 2 DDL is in [`schema/phase2.sql`](../schema/phase2.sql).

## Release discovery and provenance

The inspector derives the year repository, reads ADSB.lol's
`PREFERRED_RELEASES.txt`, lists all matching Git tags through the GitHub API,
and then reads each release's asset metadata. As of the inspection run,
ADSB.lol selected `staging-0` as the preferred source for this date.

| Release | Assets | Bytes | GiB | Treatment |
|---|---:|---:|---:|---|
| `v2026.08.20-planes-readsb-mlatonly-0` | 1 | 338,792,960 | 0.316 | Recorded, not merged |
| `v2026.08.20-planes-readsb-prod-0` | 3 | 4,072,922,112 | 3.793 | Recorded, not merged |
| `v2026.08.20-planes-readsb-staging-0` | 3 | 4,075,250,176 | 3.795 | **Selected by upstream preference** |

The selected release assets are:

| Asset | Bytes |
|---|---:|
| `v2026.08.20-planes-readsb-staging-0.tar.aa` | 2,000,000,000 |
| `v2026.08.20-planes-readsb-staging-0.tar.ab` | 2,000,000,000 |
| `v2026.08.20-planes-readsb-staging-0.tar.ac` | 75,250,176 |

This makes provenance policy explicit: Phase 2 should ingest exactly the release
selected by the upstream preference list and store the release tag, instance,
asset URLs, and byte sizes. It should not silently combine prod, staging, and
MLAT-only releases.

Primary source references:

- [ADSB.lol historical-data documentation](https://www.adsb.lol/docs/open-data/historical/)
- [ADSB.lol 2026 archive repository](https://github.com/adsblol/globe_history_2026)
- [readsb trace JSON documentation](https://github.com/wiedehopf/readsb/blob/dev/README-json.md#trace-jsons)

## Real archive and payload format

The release is one **uncompressed tar byte stream split across assets**. The
parts must be concatenated logically; each part is not an independent tar.

The selected staging archive starts with:

```text
./
./LICENSE-ODbL.txt
./traces/
./traces/3d/
./traces/3d/trace_full_a0053d.json
...
```

The aircraft entries are named `.json`, but their first bytes are the GZIP magic
number and their content is gzip-compressed JSON. Each top-level object contains
one aircraft address and one day's `trace` array. The top-level `timestamp` is a
Unix base time; element zero in each trace row is seconds after that timestamp.
The sample spans 2026-08-20 00:00:00.360 through 23:59:59.440 UTC.
All 500 files identify their producer as `readsb 3.16.15 05df27d`, and every one
of the 659,993 sampled trace rows has the documented 14-element layout.

The range-based inspection transferred 18,564,282 bytes in 507 small range
requests—about 0.46% of the 4.075 GB release. It did not persist raw archive
parts locally.

## Sample observations

The sample contains the first 500 aircraft files in archive order across the
leading hash shards. Hashing should make it broadly representative, but this is
not a formal uniform random sample; the intervals below quantify resampling
variation only.

| Metric | Observed |
|---|---:|
| Aircraft files parsed | 500 |
| Compressed aircraft payload | 17,927,136 bytes |
| Uncompressed JSON | 115,523,688 bytes |
| Trace records | 659,993 |
| Mean records per aircraft | 1,319.99 |
| Median records per aircraft | 733.5 |
| 95th percentile | 4,543.15 |
| Maximum | 9,817 |
| Aircraft with registration | 414 (82.8%) |
| Aircraft with type code | 402 (80.4%) |
| Non-ICAO `~xxxxxx` addresses | 56 (11.2%) |
| Distinct callsigns found | 862 |

Top-level field availability:

| Field | Aircraft files |
|---|---:|
| `icao`, `version`, `timestamp`, `trace` | 100.0% |
| `dbFlags` | 83.2% |
| `r` (registration) | 82.8% |
| `t` (type code) | 80.4% |
| `desc` | 74.2% |
| `ownOp` | 49.2% |
| `year` | 45.6% |
| `noRegData` | 16.8% |

Trace-row field availability:

| Field | Non-null records |
|---|---:|
| time offset, latitude, longitude, altitude/ground, flags, source type | 100.0% |
| ground speed | 99.01% |
| track | 94.59% |
| barometric vertical rate | 90.73% |
| geometric altitude | 89.05% |
| geometric vertical rate | 49.10% |
| indicated airspeed | 28.96% |
| roll | 27.54% |
| embedded aircraft-detail object | 25.01% |

Position source was predominantly `adsb_icao` (93.18%), with MLAT (2.07%),
`adsb_icao_nt` (1.97%), ADS-R, and several TIS-B forms also present. Callsign is
not a stable top-level field; it occurs as `flight` inside the intermittent
aircraft-detail object and an aircraft can have multiple callsigns in one day.

## Full-day estimates

The script bootstraps the sampled per-entry tar footprint and record counts over
the selected release's trace region.

| Metric | Estimate | Sample 95% interval |
|---|---:|---:|
| Aircraft trace files/day | 111,540 | 100,836–123,424 |
| Trace records/day | 146,851,559 | 143,491,337–150,416,161 |
| Uncompressed trace JSON/day | 25.71 GB | 25.29–26.15 GB |

These figures are suitable for capacity planning, not billing or scientific
claims. Phase 2 must replace them with exact counts from a full sequential pass.

Planning implications:

- One aircraft-day summary per trace is roughly 112,000 rows/day. At a provisional
  400 bytes per row including indexes, that is about 44.6 MB/day.
- A fully normalized position table is roughly 147 million rows/day. Even an
  optimistic 80 bytes per row is about 11.75 GB/day before normal PostgreSQL
  operational overhead is measured.
- The compressed archive expands to roughly 25.7 GB of JSON, but a streaming
  parser never needs that full expansion on disk or in RAM.
- The 500-file sample's largest trace has 9,817 observations. Parsing and
  releasing one gzip member at a time is comfortably bounded for the observed
  files.

## Exact Phase 2 storage decision

Phase 2 should create `dataset_day`, `ingestion_job`, `aircraft`,
`aircraft_day`, and configurable `aircraft_type_classification` tables. It should
not create a permanent position table yet.

Important changes from the speculative brief schema:

- Use `address varchar(7)` plus `address_kind`, not only `icao_hex char(6)`,
  because 11.2% of sampled aircraft files use readsb non-ICAO `~xxxxxx`
  addresses. A generated nullable `icao_hex` supports true ICAO lookups.
- Store registration, type, description, owner/operator, year, and DB flags in
  `aircraft_day` as a historical snapshot as well as updating the convenience
  `aircraft` record. Identity metadata is incomplete and can change.
- Store trimmed distinct daily callsigns as `text[]`; one `callsign_sample`
  would discard real variation.
- Store the position-source types seen per day. The archive mixes ADS-B, MLAT,
  ADS-R, and TIS-B observations.
- Record source gzip and expanded JSON bytes per aircraft-day. This makes future
  storage-reduction measurements exact rather than estimated.
- Keep rotorcraft classification in its own configurable table. The source's
  `t` and `desc` values are useful but incomplete, and no hard-coded helicopter
  list belongs in ingestion logic.

The Phase 2 parser should calculate, per aircraft-day:

- exact observation and valid-position counts;
- first/last timestamps and positions;
- numeric altitude min/max while treating the literal `"ground"` separately;
- maximum ground speed;
- distinct callsigns and source types;
- time observed;
- approximate great-circle distance between consecutive valid positions.

## Phase 2 transaction and streaming contract

1. Discover and persist the preferred release manifest.
2. Stream every asset to disk with resumable `.part` files and byte validation.
3. Present the parts as one logical tar stream.
4. For each trace entry, decompress and parse only that aircraft, derive one
   summary, add it to a bounded database batch, then release the object.
5. Insert through a temporary/staging table inside the date replacement
   transaction described at the end of `schema/phase2.sql`.
6. Update exact counts and mark `PROCESSED` only after the transaction commits.
7. Delete raw parts only after successful commit; retain them on any failure.

This preserves the brief's idempotency and failure-recovery requirements while
keeping the analytical schema small enough to evaluate before deciding whether
sampled or compressed tracks are commercially useful.
