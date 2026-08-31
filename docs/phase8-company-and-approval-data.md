# Phase 8: companies, operators, MROs and approval scope

Phase 8 adds a provenance-backed company layer without treating a company name
from ADS-B metadata as regulatory truth. The core `company` table deliberately
has the two simple role flags required by the application:

- `is_operator`
- `is_mro`

A company may be both. Roles imported from one source are combined rather than
removed by a later source that knows only about the other role.

## Relational model

The supporting tables retain the detail needed to answer useful questions:

- `company_alias` stores legal, trading, former and source names;
- `company_site` stores physical locations and optional links to Heligent's
  airport reference, including base- and line-maintenance flags;
- `regulatory_approval` stores authority, approval type and certificate number,
  status and validity;
- `company_site_approval` records which sites operate under an approval;
- `approval_capability` stores rating/class, aircraft, engine, component or
  specialist scope, raw limitations, and base/line privileges;
- `company_aircraft_assignment` links an operator, owner or manager to an
  observed address and/or a source-reported registration;
- external-identifier and import-batch tables make refreshes idempotent and
  retain source URLs, hashes, row snapshots and import outcomes.

`company_directory`, `airport_company_directory`, and
`current_company_aircraft` are convenience views for later UI, analytics and
natural-language work. An approval capability's `aircraft_type_code` is kept
separate from its free-text manufacturer/model/limitation because regulator
files do not consistently use ICAO designators.

## Natural-language querying

Imported records are available through the normal query box with three closed,
server-validated operations:

- company directory searches by name, MRO/operator role and site/address text;
- approval searches by company, certificate number, location and status;
- capability searches by company, location, capability kind, rating, aircraft
  type/model, manufacturer and raw limitation text.

Capability matching ignores punctuation, so `EC135`, `EC-135` and similar
spreadsheet/PDF variants match the same scope text. Results reproduce the
regulatory scope and approval number, and are labelled as reference data rather
than observed activity. Imported sites are not joined to hub traffic until they
have an `airport_ident`; companies are not joined to tails until an explicit
`company_aircraft_assignment` has been imported.

## Safe CSV workflow

The canonical template is `schema/company-import-template.csv`. One row can
carry only a company, or additionally a site, approval, capability and aircraft
assignment. Repeat the company and approval fields for multiple sites,
capabilities or registrations.

Validate without a database connection:

```powershell
python -m adsb_ingest.companies validate-csv `
  --file .\data\reference\uk-caa-part145-normalized.csv
```

Preview an import without changing PostgreSQL:

```powershell
python -m adsb_ingest.companies import-csv `
  --file .\data\reference\uk-part145-companies-import.csv `
  --source-code UK_CAA_PART145 `
  --source-name "UK CAA Part 145 approved organisations" `
  --source-kind REGULATOR `
  --authority-code UK_CAA `
  --source-url "https://www.caa.co.uk/commercial-industry/aircraft/airworthiness/design-maintenance-and-production-organisation-and-maintenance-programme-approvals/approved-airworthiness-organisations/" `
  --dry-run
```

Remove `--dry-run` after validation and set `DATABASE_URL` in the same terminal.
The importer validates the complete file first, then performs an atomic upsert:

```powershell
$env:DATABASE_URL = "postgresql://user:password@127.0.0.1/heligent_adsb"

python -m adsb_ingest.companies import-csv `
  --file .\data\reference\uk-caa-part145-normalized.csv `
  --source-code UK_CAA_PART145 `
  --source-name "UK CAA Part 145 approved organisations" `
  --source-kind REGULATOR `
  --authority-code UK_CAA `
  --source-url "https://www.caa.co.uk/commercial-industry/aircraft/airworthiness/design-maintenance-and-production-organisation-and-maintenance-programme-approvals/approved-airworthiness-organisations/"
```

Stable `source_company_id`, `source_site_id`, `source_approval_id` and source
capability/assignment IDs are strongly recommended. Reimporting the same file
updates the same entities and records a new batch; it does not duplicate them.
When an ID is absent, the importer derives a deterministic fallback. A curated
`company_key` can deliberately merge exact entities encountered in different
sources. Unknown columns, malformed booleans, partial coordinates, invalid
dates and capabilities without an approval are rejected with a CSV row number.

Date fields are deliberately tolerant of Excel exports. The importer accepts
ISO dates/timestamps, UK day-first values such as `20/08/2026`, common Excel
month-name forms such as `20-Aug-26`, date/time values, and Windows/1900-system
Excel serial numbers (including fractional time). Ambiguous slash dates are
interpreted day/month/year; an unambiguous US value such as `8/20/2026` is also
accepted. PostgreSQL still receives a real date or UTC timestamp rather than
the original display text.

Imports are non-destructive: disappearance from a new source snapshot does not
automatically revoke or delete a record. Regulatory status should be imported
explicitly as `SUSPENDED`, `REVOKED` or `EXPIRED`. This avoids interpreting a
temporary publishing omission as a legal-status change.

## Recommended source order

1. **UK CAA Part 145 approved organisations** is the best UK MRO seed. The CAA
   publishes separate current UK, overseas, USA and Brazil Part 145 PDF lists.
   The reports include approval numbers, organisational data, sites and ratings,
   including aircraft types. The PDF layout needs a source-specific normalizer,
   but its content maps closely to the Phase 8 schema.
2. **UK CAA AOC holders** is the best first UK operator seed. Its public report
   includes legal company, trading name, contact, approved aircraft types and
   AOC number. It establishes operator status and type scope, but does not
   provide a tail-by-tail fleet.
3. **FAA Part 135 Operators and Aircraft** is especially valuable because the
   FAA publishes a weekly spreadsheet searchable by operator and N-number. It
   can populate both operator companies and aircraft assignments.
4. **EASA foreign Part-145 scope** is the strongest broad MRO capability seed.
   Its interactive dataset supports JSON/XLS export and exposes approval number,
   organisation, country, rating, limitation, aircraft line/base and NDT method.
   It covers organisations for which EASA is the competent authority, mainly
   organisations outside EU member-state territories; member-state approvals
   still need national-authority sources.
5. **FAA releasable aircraft registry** is a useful daily registration and
   owner-enrichment feed. A registered owner is not necessarily the operator,
   so it should create `OWNER` assignments rather than set operator status.

The FAA AVInfo repair-station directory is useful for lookup and verification,
but its public landing page currently emphasizes interactive search rather than
a stable documented bulk export. It should not be the first unattended importer
unless a supported download endpoint is confirmed.

## Next source-adapter work

The generic contract intentionally separates acquisition/parsing from database
upsert. Source adapters should download to `data/reference`, calculate a source
hash, transform regulator-specific fields into the canonical CSV contract, run
validation, and only then import. The first two useful adapters are:

1. UK CAA AOC PDF to companies, aliases, AOC approvals and aircraft-type scope;
2. UK CAA Part 145 PDF to companies, sites, approvals, ratings and capabilities.

FAA Part 135 XLSX and EASA JSON/XLS can follow using the same import boundary.
