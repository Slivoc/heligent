# Heligent ADS-B historical analytics

The repository now contains Phase 1 source inspection, the Phase 2 one-day
ingestion pipeline, the Phase 3 data-management application, the Phase 4
coverage-aware analytics workspace, the Phase 5 natural-language query
prototype, the Phase 6 airport-movement candidate model, the Phase 7
public-demo boundary, the Phase 8 company/operator/MRO foundation, and Phase 9
manual customer/base curation. Phase 11 adds authoritative national aircraft
registry snapshots for Canada, the United States and Australia. Phase 15 adds
compact flight episodes, repeat airport visits, and provisional MRO-stay
intervals. The retained
data deliberately optimizes for:

- airport/hub traffic by tail and aircraft type;
- estimated tail active, airborne, and ground-active hours;
- compact daily summaries and evidence-bearing episodes rather than positions;
- explicit source provenance, retries, idempotency, and raw-file deletion.

Individual ADS-B positions exist only while one aircraft gzip member is being
summarized. They are not inserted into PostgreSQL; inferred flights and airport
visits retain their evidence, coverage gaps, and confidence instead.

## Raspberry Pi raw archive

`adsb-archive` is a PostgreSQL-free, SSD-oriented raw archive worker. It keeps a
durable SQLite queue, priority-downloads the newest available completed day,
resumes partial assets, verifies release SHA-256 digests, and writes a manifest
without deleting the release. Older dates and ranges can be queued explicitly:

```bash
export ADSB_ARCHIVE_ROOT=/mnt/adsb-archive
adsb-archive queue-latest
adsb-archive queue-range 2026-08-01 2026-08-07
adsb-archive worker
```

Pi systemd units, OS and SSD setup, disk sizing, integrity checks, re-parsing
instructions, the private read-only feeder API and SSH-over-Tailscale guidance are in
[`docs/raspberry-pi-raw-archive.md`](docs/raspberry-pi-raw-archive.md).

## Aviation intelligence API

The authenticated, read-only service-to-service API for historical aircraft
activity is currently available through the `heligent-intelligence-api`
command. The current v1 contract begins with helicopter-specific operations,
while the underlying ingestion and analytics already support fixed-wing
aircraft. It binds to `127.0.0.1:5100` by default and requires `DATABASE_URL`
plus a separate `HELIGENT_API_TOKEN`. A systemd unit is provided at
[`deploy/vps/heligent-intelligence-api.service`](deploy/vps/heligent-intelligence-api.service).

Its versioned endpoints provide coverage, helicopter activity by registration,
operator or airport, batched known-fleet daily facts, per-tail daily evidence,
and regional aircraft/type/observed-hub rankings.
The complete contract is in
[`docs/intelligence-api.md`](docs/intelligence-api.md).
The Sproutt integration contract and product-ownership boundary live in
`C:\crm\docs\heligent_api_integration.md` in the sibling CRM workspace.

## VPS deployment and colleague access

The production shape uses Ubuntu Server 24.04 LTS and PostgreSQL. The
maintenance UI is public only through an ngrok OAuth gateway; signed-in emails
must also exist in Heligent's local `VIEWER`, `ANALYST`, or `ADMIN` allow-list.
The Sproutt API remains private through Tailscale Serve plus its bearer token,
or through loopback while both separate products temporarily share a VPS. The
installer has an explicit Sproutt co-host mode with systemd resource controls.
Install scripts, hardened systemd units, configuration templates, migration and
user-management commands, firewall checks, backups, and the complete runbook
are in [`docs/vps-deployment.md`](docs/vps-deployment.md).

## Phase 11 authoritative aircraft identity

**Tools → Sources & coverage** now tracks register snapshots, source field
completeness, company/approval imports and regional tail/type gaps. Each source
has its own workspace, shared notes and review target. ADSBdb and HexDB support
cached individual-hex checks; planned bulk and national-register adapters are
labelled separately. See [the source tools guide](docs/source-tools.md).

The Transport Canada adapter downloads and validates the official current
Canadian Civil Aircraft Register (CCAR) ZIP, stores each immutable snapshot,
and compares its registration, Mode S address, model and aircraft category to
the ADS-B metadata. It does not import the owner file as operator evidence:
registered owner and operational control are different claims.

Install the project, set `DATABASE_URL`, then preview and import the current
official snapshot:

```powershell
heligent-aircraft-registry sync-canada --dry-run
heligent-aircraft-registry sync-canada
heligent-aircraft-registry audit-canada --limit 50
```

To use an already downloaded official ZIP or give the snapshot an explicit
as-of date:

```powershell
heligent-aircraft-registry sync-canada `
  --file .\data\reference\transport-canada\ccar-2026-08-24.zip `
  --snapshot-date 2026-08-24
```

`--dry-run` downloads and fully validates the archive without changing
PostgreSQL. Imports are atomic and idempotent by source-file SHA-256. The
original `aircraft_day` values remain intact; analytics read the resolved
`aircraft_day_identity` view. When CCAR contradicts an ADS-B aircraft category
or registration, the suspect ADS-B type code is suppressed rather than
silently replaced with a guessed ICAO designator. The raw claim remains in the
view's `source_*` fields and appears in `aircraft_identity_conflict` for audit.

The source is Transport Canada's public CCAR download and is used under the
Government of Canada Open Data Licence Agreement for Unrestricted Use.
Attribution: “Includes data provided by the Government of Canada; no
endorsement is implied.”

The same registry layer supports the FAA's daily Releasable Aircraft Database.
Only the current master and aircraft make/model reference files influence
identity resolution. Dealer, document-index, reserved-number, deregistered and
registrant name/address data are not imported. Rows are eligible only when the
FAA status describes a valid, pending, manufacturer/dealer, or documented
non-citizen registration state.

```powershell
heligent-aircraft-registry sync-faa --dry-run
heligent-aircraft-registry sync-faa
heligent-aircraft-registry audit-faa --limit 50
```

To import a retained official ZIP with an explicit snapshot date:

```powershell
heligent-aircraft-registry sync-faa `
  --file .\data\reference\faa\faa-aircraft-registry-2026-08-24.zip `
  --snapshot-date 2026-08-24
```

The FAA certificate issue date (or last-action date when no certificate date is
published) prevents a current N-number/Mode S assignment from rewriting older
observations. Certificate expiration bounds are also retained. Attribution:
“Source: Federal Aviation Administration Civil Aviation Registry.” As with
Canada, FAA registrant data is not operator evidence.

CASA's Australian Civil Aircraft Register has no Mode S address, so its adapter
matches `VH-` registrations after punctuation-insensitive normalization. CASA's
published ICAO designator and airframe category can authoritatively replace a
conflicting ADS-B type claim while `source_*` fields retain the original claim.
The register explicitly publishes a Registered Operator, so that claim appears
in the Operators page/API and natural-language operator queries with CASA
provenance. It is not silently merged into the CRM company directory, and
registration-holder or address fields are not imported.

```powershell
heligent-aircraft-registry sync-casa --dry-run
heligent-aircraft-registry sync-casa
heligent-aircraft-registry audit-casa --limit 50
```

To import a retained official ZIP with an explicit snapshot date:

```powershell
heligent-aircraft-registry sync-casa `
  --file .\data\reference\casa\casa-aircraft-register-2026-08-24.zip `
  --snapshot-date 2026-08-24
```

The source is CASA's current-aircraft register and is used under CC BY 4.0.
Attribution and the fact that fields/category mappings were normalized are
stored with every import batch.

## Phase 8 company, operator and MRO data

The database now has a normalized, provenance-backed company directory. Every
company has the app-facing `is_operator` and `is_mro` booleans, while related
tables retain aliases, sites, regulatory approvals, aircraft/engine/component
capabilities, base/line privileges and operator-to-aircraft assignments.

Companies, sites, registry operator claims and aircraft assignments also carry
a normalized world region (`AFRICA`, `ASIA`, `EUROPE`, `NORTH_AMERICA`,
`SOUTH_AMERICA` or `OCEANIA`). Assignment regions use company country when it is
known; otherwise they fall back to registration jurisdiction and retain
`region_basis=REGISTRATION_PREFIX`. Natural-language company and operator
queries accept terms such as “European”, “Oceania” and “the Americas”.

Use the validated, idempotent CSV import contract for manual curation and future
CAA, EASA and FAA source adapters:

```powershell
python -m adsb_ingest.companies validate-csv `
  --file .\schema\company-import-template.csv
```

See `docs/phase8-company-and-approval-data.md` for the schema, import commands,
source recommendations and important owner-versus-operator limitations.

The local interface also has an **Operators** surface for current sourced
aircraft assignments. It can be searched by company, registration, Mode S
address or source and keeps the role, confidence, validity window and source
row identifier visible. The natural-language screen reads the same records, so
questions such as `Who operates G-TEST?` and `Which aircraft are assigned to
Bristow as operator?` return imported assignment evidence. These claims are
not treated as proof of operational control for a particular flight.

## Phase 9 customers and addresses of interest

The local admin interface now has a **Customers** surface. Search imported
company sites, flag the company as a customer, flag an individual address for
watching, attach an internal note, and manually link the site to an airport from
Heligent's reference data. The selected date then shows airport-level arrival
and departure candidates, aircraft types and tails alongside the MRO approval
scope. The interface explicitly labels this as activity at the associated
airport, not proof that an aircraft visited the company premises.

See `docs/phase9-customer-base-curation.md` for the workflow and attribution
boundary.

## Phase 5 natural-language analytics

The application now opens on a conversation-first screen. A user can ask about
hub traffic, tail activity hours, aircraft types, trends, totals, or classified
helicopters in plain English and receive a coverage statement, short answer,
constrained chart, and inspectable table.

Set the API key in the server process before launching the application:

```powershell
$env:OPENAI_API_KEY = "your-api-key"
$env:DATABASE_URL = "postgresql://user:password@localhost/adsb_analytics"
adsb-web
```

`OPENAI_MODEL` is optional and defaults to `gpt-5.4-mini`. The model receives a
small schema of read-only `nl_*` aviation views and produces one `SELECT` in a
strict structured plan. The server parses and allowlists the SQL, verifies date
and row bounds, checks PostgreSQL's planned cost, and executes it in a timed
read-only transaction. Database credentials, tail rows, and query results are
not sent to the model. See
`docs/phase5-natural-language-analytics.md` for the complete contract.

## Phase 7 public mobile demo

The focused `/demo` route can run in an explicit public mode that blocks every
admin and ingestion endpoint, disables debug dumps, applies client/global query
budgets, caps concurrent model calls, and emits production security headers.
Accounts are intentionally deferred for the small demonstration audience. See
`docs/phase7-public-demo.md` for the process split and VPS boundary.

## Phase 4 analytics Explorer

The application now opens on a basic analytics workspace for the latest local
day. It provides coverage-aware latest-day, 7-day, 30-day, and custom snapshots;
daily unique-tail and observation charts; aircraft-type metrics; busiest-hub
and most-active-tail tables; and a conservative classified-rotorcraft view.

Every partial date range is labelled before its charts and tables are shown.
Flight and repeat-visit processing is documented separately from these original
daily rollups. See
`docs/phase4-basic-analytics.md` for metric and classification details.

## Phase 3 data management

The local web application provides a UTC-date picker, missing-day range queue,
monthly status calendar, live stage messages, failure details, retry,
reprocessing, cancellation of queued work, raw-retention control, and recent
queue history. One database-backed worker processes dates sequentially.

```powershell
$env:DATABASE_URL = "postgresql://user:password@localhost/adsb_analytics"
adsb-web
```

Open `http://127.0.0.1:5080`. The server binds to loopback by default and
applies the idempotent Phase 3 queue migration at startup. The compiled web
bundle is included in the Python package; Node.js is needed only when changing
the frontend source under `web/`.

Open `http://127.0.0.1:5080/demo` for the focused mobile demonstration. It
contains only Heligent branding, a question composer, the answer and compact
results, plus optional follow-up suggestions; ingestion and analytics controls
remain in the main application.

For local frontend development, run `npm run dev` in `web/` while `adsb-web`
is running. A production frontend rebuild uses `npm run build:spa`.

## Phase 2 quick start

Python 3.11+ and PostgreSQL 15+ are required.

```powershell
python -m pip install -e .
$env:DATABASE_URL = "postgresql://user:password@localhost/adsb_analytics"

adsb-ingest init-db
adsb-ingest ingest-day --date 2026-08-20
adsb-ingest status --date 2026-08-20
```

The ingestion command performs the complete workflow:

1. discovers the date's current ADSB.lol preferred release;
2. imports the public-domain OurAirports reference dataset;
3. streams and resumes release-asset downloads;
4. verifies GitHub's SHA-256 digest for every asset;
5. parses the split tar sequentially, one gzip aircraft trace at a time;
6. replaces that date's derived rows in one PostgreSQL transaction;
7. deletes the raw assets only after the transaction commits.

Use `--keep-raw` to retain source assets for parser debugging. Use
`--reprocess` to deliberately replace an already processed date. An interrupted
download resumes its `.part` file; an interrupted parse keeps the verified raw
assets and rolls back all replacement rows.

## What is stored

`aircraft_day` has one row per tail/address/day, including:

- registration and aircraft-type snapshot;
- observation count and observed span;
- estimated active, airborne, and ground-active seconds;
- distinct callsigns and position-source types;
- number of matched airports;
- first/last position and altitude/speed extrema;
- compressed and expanded source bytes.

`aircraft_airport_day` has one row per tail/airport/day, including observed
ground time, ground-active time, closest distance, aggregated repeat visits,
conservative arrival and departure candidate counts, evidence method, and a
single primary airport for the tail/day.

`aircraft_flight_segment` retains inferred airborne episodes with separate
observed and elapsed duration, origin/destination evidence, confidence and
coverage flags. `aircraft_airport_visit` retains repeated airport contacts and
open day boundaries. See
[`docs/phase15-flight-visits-and-maintenance-foundation.md`](docs/phase15-flight-visits-and-maintenance-foundation.md)
for the rebuild procedure and provisional Maintenance Pulse views.

The views `aircraft_type_day_metrics`, `airport_day_metrics`, and
`airport_day_type_metrics` provide ready-made type and hub rollups. For
example:

```sql
SELECT *
FROM airport_day_type_metrics
WHERE utc_date = DATE '2026-08-20'
ORDER BY unique_aircraft DESC
LIMIT 50;
```

```sql
SELECT
    ad.registration,
    ad.address,
    ad.type_code,
    ad.active_time_seconds / 3600.0 AS active_hours,
    ad.airborne_time_seconds / 3600.0 AS airborne_hours,
    aad.airport_ident,
    aad.presence_count
FROM aircraft_day ad
LEFT JOIN aircraft_airport_day aad
  ON aad.dataset_day_id = ad.dataset_day_id
 AND aad.address = ad.address
 AND aad.is_primary_airport
WHERE ad.utc_date = DATE '2026-08-20';
```

## Metric definitions

These are observational estimates, not certified logbook hours:

- **Tail count:** unique ADS-B addresses for the UTC day. Registration is an
  optional metadata snapshot, so the count should not be read as a guaranteed
  one-to-one count of legally registered airframes.
- **Active time:** consecutive observation intervals of at most 120 seconds
  where the tail is airborne or either endpoint reports at least 5 knots.
- **Airborne time:** qualifying intervals where either endpoint is not marked
  `ground` and has numeric altitude.
- **Ground-active time:** qualifying ground-to-ground intervals with at least
  5 knots at either endpoint.
- **Airport presence:** one compatibility link per tail/airport/day, aggregated
  from distinct visit episodes using direct ground evidence, tight internal
  low/slow contacts, and broader low/slow evidence only at trace boundaries.
- **Movement candidate:** an inferred arrival or departure attached to a visit
  that crosses the airport hysteresis radius. A tail can now produce multiple
  candidates at one airport/day. These are conservative analytical indicators,
  not certified airport records.
- **Flight episode:** a run of airborne observations separated by ground/airport
  contact or a 30-minute discontinuity. Observed time excludes gaps over 120
  seconds; elapsed time retains the inferred boundary span and reports the
  unobserved portion explicitly.
- **Primary airport:** the strongest evidence-ranked airport for the tail/day;
  direct ground evidence wins before duration, movement count, observation
  count, and proximity tie-breakers.

Defaults can be changed with the `ingest-day` gap/speed options. Airport radii
live in `src/adsb_ingest/airports.py`, separate from the summarization logic.
Point-to-point distance is intentionally disabled; `--calculate-distance` is
available only for experiments that justify its substantial CPU cost.

OurAirports updates its public-domain CSV daily. Its URL, SHA-256, download time,
and imported row count are stored in `reference_dataset`.

## Phase 1 inspector

The bounded inspector remains available:

```powershell
adsb-phase1 --date 2026-08-20 --sample-aircraft 500 `
  --output reports/phase1-2026-08-20.json
```

See `docs/phase1-findings-2026-08-20.md` for the original evidence,
`docs/phase3-data-management.md` for the UI/queue contract,
`docs/phase4-basic-analytics.md` for the analytics contract, and
`docs/phase5-natural-language-analytics.md` for the natural-language contract.
The movement inference is documented in
`docs/phase6-airport-movement-candidates.md`.
See `schema/phase2.sql` for the complete fresh-database schema.

## Tests

Unit tests do not require network access or PostgreSQL:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

To include the database replacement/rollback test, point
`TEST_DATABASE_URL` at a disposable PostgreSQL database. The test drops and
recreates its `public` schema:

```powershell
$env:TEST_DATABASE_URL = "postgresql://user:password@localhost/adsb_test"
python -m unittest discover -s tests -v
```
