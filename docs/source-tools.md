# Sources and identity coverage

Open **Tools → Sources & coverage** (`/#tools`). Every source has a directly
linkable workspace at `/#tools/source/SOURCE_CODE`. This is the inventory of
available importers, stored evidence and planned adapters. It does not infer that
an installed importer is running on a schedule.

## What is visible

- Canada CCAR, US FAA and Australia CASA register importers, with snapshot dates,
  import/fetch times, row counts, original filenames, hashes and stored batch status.
- Field completeness for the latest successful register snapshot: hex, tail,
  ICAO designator, category and rotorcraft counts.
- LBA's selected-organisation scraper, most recent saved search and import history.
- All company sources discovered in `company_data_source`, including CSV imports
  created outside Tools. Imported site countries are not a promise of national coverage.
- Processed ADSB.lol archive coverage and imported reference datasets.
- Per-source notes and review intervals shared between analysts. The default
  intervals are local review targets, not promises about upstream update frequency.
- Separate individual-hex workspaces for ADSBdb and HexDB, plus the planned bulk
  and national-register adapters described below.

Source freshness uses the latest successful **source snapshot date**. A download
today of a month-old snapshot remains old. Missing source dates stay unknown;
future dates are flagged. The generic company CSV importer does not record an
upstream snapshot date, so a recent company import does not turn its freshness
green. The inventory does not monitor cron/systemd timers. Older importers can
roll failed attempts back completely; displayed history is the available evidence,
not a complete execution log. History shows the latest 25 stored batches; a prior
successful snapshot is still used if newer attempts failed.

Registry snapshot dates are the as-of dates recorded by the importer (or supplied
with `--snapshot-date`); publisher revision dates are not independently verified.
When importing a retained file, supply its actual as-of date to avoid labelling
an old file with today's date.

## Geographic and aircraft gaps

The gap report defaults to the latest processed week and accepts at most 31 days.
It uses `aircraft_day_identity`, includes only processed, position-bearing days,
and counts distinct hexes with at least one matching day. Missing tail, missing
ICAO type, missing both, unknown category, helicopters without tails and tails
recovered by registers are reported separately. A known rotorcraft category does
not require a populated ICAO designator.

Regions come from airport visits, **not** the registration prefix or guessed
hex country. Aircraft without usable airport-region evidence have their own row.
An aircraft visiting several regions contributes to each region but once to the
worldwide total. Fields can be missing on different days, so the gap counts overlap.
The report counts observed metadata gaps, not national-fleet completeness or
receiver coverage. Countries with importers are shown alongside the report:
Canada/US do not cover all North America; CASA does not cover all Oceania.

**Review tails** and **Review types** preserve the period and region and open
the corresponding identity gap with all aircraft categories selected. The review
page also retains the original ADS-B-tail-missing mode for auditing historical
metadata that has since been enriched by a register. Its helicopter filter now
uses resolved, dated registry category. With unknowns included, classified
helicopters appear first, then unknowns; each group is sorted by estimated airborne
hours before pagination. It shows original types, resolved category, registry
source, resolved tails and current registration separately.

## Individual API checks

ADSBdb and HexDB return current claims for one six-digit ICAO hex. Use the source
workspace or **Cross-reference** on an aircraft row. Lookups do not update the
aircraft table, assign a historical tail or silently enrich analytical caches.
An analyst can compare the result with dated evidence and use the existing
identity-preview workflow when appropriate. Owner names are not stored as operators.

Lookups require ANALYST/ADMIN, fixed HTTPS endpoints and a validated hex. Responses
are bounded by size and time; redirects are rejected. Found records and misses
are cached in PostgreSQL for 30 days, failures for five minutes. A database lock
coordinates callers across app processes: at most one in-flight call per provider,
five seconds between new calls and 60 new calls per provider in a rolling day.
Status, normalized claims, fetch time, source URL and requesting actor are retained.
HTTP failures, malformed responses and mismatched hexes never become identities.
Per-record source revision dates remain unknown even after a successful lookup.

## Which sources to add next

1. **tar1090-db** is the priority bulk candidate source. Its repository publishes
   `aircraft.csv.gz` on the `csv` branch. Implement a bounded download/file import,
   version/hash retention, column validation and candidate comparisons against
   unresolved hexes. Keep candidates separate from authoritative dated identity.
   [Publisher repository](https://github.com/wiedehopf/tar1090-db).
2. **Mictronics** publishes JSON/ZIP exports under the Open Data Commons Attribution
   License and states a weekly export cadence. tar1090 uses this upstream, so these
   should share a source-family identifier; matching claims are not independent
   confirmation. The direct export is an alternative input, not a second vote.
   [Mictronics exports and licence](https://github.com/Mictronics/aircraft-database).
3. **ADSBdb** provides aircraft responses with registration, ICAO type and manufacturer.
   It is now available for individual checks. Whole-database coverage and per-record
   revision dates are not assumed. [API contract](https://github.com/mrjackwills/adsbdb).
4. **HexDB** is also available for individual checks. Its publisher explicitly asks
   clients not to scrape the database and distinguishes daily refreshes from monthly
   upstream changes. Do not add a background hex sweep. [Publisher guidance](https://hexdb.io/).
5. **UK CAA G-INFO** is a priority national gap. The monthly bulk product is supplied
   as Excel under specific licensing conditions. Obtain appropriate rights and a
   sample, verify whether a hex field is included, then implement a dedicated file
   mapping/preview tool. [CAA product and conditions](https://www.caa.co.uk/aircraft-register/g-info/g-info-download/).
6. **OpenSky metadata** offers another downloadable CSV candidate source. Confirm
   applicable terms, snapshot dates, overlap and column semantics before importing.
   [OpenSky aircraft metadata](https://opensky-network.org/data/aircraft).

The first two bulk adapters, G-INFO and OpenSky are visibly **planned**, not running.
There are currently no national identity adapters for Europe, Africa, Asia or South
America. The LBA organisation directory does not fill Germany's aircraft-register gap.
CASA matches known VH registrations and cannot derive a missing tail directly from hex.
Provider documentation reviewed on 2026-09-12; this is not an upstream data timestamp.

## Extending and deploying

`source_catalog.py` declares known capabilities, source-family relationships,
scope and next actions. `source_tools.py` adds actual database evidence and discovers
company/reference sources. Add each new source's own parser and preview/import
boundary, retain input hashes and source dates, and expose it in its workspace.
Do not silently convert a planned adapter into an active feed merely by adding a card.

Migration **phase22.sql** adds only shared source settings and lookup evidence, plus
lookup-cache indexes. It is registered in normal startup and `heligent-migrate`.
Deploy the Python changes, migration and rebuilt SPA through the usual process.
No archive reprocessing, new API credentials or Pi changes are needed. Deployment
does not run imports or rewrite historical identities.

Validation: `python -m unittest discover -s tests`, the PostgreSQL source/unidentified
integration tests with a dedicated `TEST_DATABASE_URL`, `npm run lint`, `npm test`,
and `node tests/source-tools.browser.cjs` (Playwright; isolated local fixture server).

During this change, the focused source/identity integration tests, Python unit
suite, lint, frontend builds/tests and both Tools browser suites passed. The full
existing PostgreSQL suite has a reproducible CASA/operator fixture conflict
(`Pulse Air` versus the expected CASA operator), also present when the new source
tests are excluded. Full `tsc --noEmit` reports existing missing Cloudflare worker
types in `db/index.ts` and `worker/index.ts`; the two production builds pass.
