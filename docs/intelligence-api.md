# Aviation-intelligence API v1

This is Heligent's read-only product boundary for Sproutt and future clients.
It exposes stable aviation facts and supported rankings, not database tables,
raw ADS-B files, arbitrary SQL, live positions, tracks or confirmed flight
legs.

## Transport and authentication

The service binds to `127.0.0.1:5100`. When Heligent and Sproutt temporarily
share a VPS, Sproutt uses that loopback address directly. On separate hosts it
is published privately with Tailscale Serve. It is never exposed through a
public nginx site or Tailscale Funnel. Every `/api/v1/*` request requires:

```http
Authorization: Bearer <HELIGENT_API_TOKEN>
Accept: application/json
X-Request-ID: <client-generated UUID>
```

`GET /health` is unauthenticated. Successful operations return `data` and,
where relevant, `meta`. The response echoes or creates `X-Request-ID`. Errors
use:

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "Both from and to dates are required"
  }
}
```

Dates are completed historical UTC dates, inclusive, in `YYYY-MM-DD` form.
Ranges are capped at 366 days and list limits at 500. Registrations are
case-insensitive and punctuation-insensitive for matching; responses retain the
stored display form.

## Operations

### Coverage

`GET /api/v1/coverage` returns earliest/latest processed dates and the exact
available-date set. Consumers must treat missing coverage as unknown, not zero
activity.

### Known-fleet daily facts

`POST /api/v1/aircraft/daily-activity` is the preferred Sproutt synchronization
operation. It supports all aircraft categories and accepts 1-100 registrations.

```json
{
  "from": "2026-08-01",
  "to": "2026-08-28",
  "registrations": ["G-TEST", "G-ABCD"],
  "category": "ALL"
}
```

Each returned activity day includes:

- UTC date, ICAO address, registration, category and type snapshot;
- resolved operator, country, region and source;
- identity source/status;
- observations and estimated active, airborne, ground-active and observed
  hours;
- the parser version plus flight-segment count and separate observed, elapsed,
  and unobserved segment hours on rebuilt dates;
- estimated distance, airport-link/repeat-visit counts and candidate movements;
- airport identities and evidence; and
- `data_revision`, which changes when the dataset, identity or resolved
  operator cache is refreshed.

Use `(source, utc_date, address)` as the consumer-side fact key. Always inspect
`meta.coverage` and `meta.registrations_without_activity`.

`airborne_hours` remains the conservative 120-second-continuity metric for API
compatibility. `elapsed_flight_hours` is the inferred takeoff-to-landing total;
compare it with `observed_flight_hours` and `unobserved_flight_hours` before
selecting a utilisation measure. Legacy dates return a null
`derivation_version` and zero episode fields until reprocessed.

### Regional aircraft rankings

```http
GET /api/v1/regions/EUROPE/aircraft/rankings?from=2026-08-01&to=2026-08-28&category=ROTORCRAFT&metric=active_hours&operator_status=ALL&type_status=ALL&limit=100
```

Supported aircraft metrics are `active_hours`, `airborne_hours`,
`observations`, `movement_candidates` and `active_days`. Operator status is
`ALL`, `MATCHED` or `UNMATCHED`; type status is `ALL`, `KNOWN` or `UNKNOWN`.
`operator_status=UNMATCHED` supports Heligent's operator-research workflow.

### Regional type breakdown

```http
GET /api/v1/regions/EUROPE/types?from=2026-08-01&to=2026-08-28&category=ALL&limit=100
```

Returns known type codes plus an explicit `UNKNOWN` bucket, with unique
aircraft, active days, observations, active/airborne hours and candidate
movements.

### Regional observed-hub rankings

```http
GET /api/v1/regions/EUROPE/airports/rankings?from=2026-08-01&to=2026-08-28&category=ROTORCRAFT&metric=movement_candidates&compare_previous=true&limit=10
```

Hub metrics are `movement_candidates`, `arrival_candidates`,
`departure_candidates`, `ground_observations` and `unique_aircraft`. A previous
comparison uses the same-length period immediately before `from`. Rows include
airport identity/location, known/unknown type-aircraft counts, up to five
leading types, the selected previous metric and its absolute change.

These are the **busiest observed hubs** within Heligent's available ADS-B
coverage. Candidate arrivals/departures are inferred evidence, not audited
airport movement counts.

### Helicopter aggregate and detail operations

- `GET /api/v1/helicopters/activity`
- `POST /api/v1/helicopters/activity`
- `GET /api/v1/helicopters/{registration}/days`
- `GET /api/v1/airports/{airport}/activity`

The activity operation aggregates one row per rotorcraft and filters by dates,
registration, operator fragment and airport. The per-registration operation
returns daily activity and airport evidence. The airport operation returns
helicopter-only daily totals plus ranked tails and types. These are useful for
interactive and compatibility reports; use the category-neutral daily endpoint
for Sproutt persistence.

## Maintenance organisations, recorded scopes and geographic coverage

These operations use the same bearer authentication as activity endpoints and
execute bounded, repeatable-read, read-only database transactions. They perform
no downloads, source imports, geocoding or mapping edits. They describe today's
recorded approval status and validity, with separate source-verification dates;
historical `as_of` and other unknown query parameters are rejected.

- `GET /api/v1/companies?query=ADAC&role=MRO&country=DE&limit=50`
- `GET /api/v1/companies/123/maintenance-capabilities?kind=ALL&limit=100`
- `GET /api/v1/maintenance-coverage?kind=ALL&geography=site_country`

The company search returns `data` as an array of stable `id`, `name`,
`legal_name`, `trading_name`, `country_code`, `is_mro` and `approval_count`.
It includes active companies with recorded Part-145 scope, even if an older
import omitted the company's MRO flag. Aliases are searchable. `country` means
company domicile; `site_country` requires a recorded physical-site country and
never falls back to domicile. Optional `source`, `authority` and `kind` filters
require matching active scope records. AOC scope is excluded throughout.

The capabilities response has `data.company`, `data.capabilities` and
`data.kind_counts`. Each row preserves the capability/approval IDs, source code,
authority/number, recorded status and validity dates, `approval_current`,
verification date, source URL, rating, model/manufacturer/limitation wording,
base/line flags and raw source aircraft code. `scope_level` is `COMPANY` or
`SITE`; `site` is null or the particular recorded site. Company-wide scope is
never promoted to every facility.

`mappings` retains effective local/shared interpretations, review level,
restrictions, review dates/evidence, active and stale flags. Internal reviewer
identities, notes and stored audit snapshots are omitted. A local override wins
even if withdrawn or stale. `type_interpretation` is `SOURCE_CODE`,
`REVIEWED_TYPE`, `POSSIBLE_FAMILY` or `UNMAPPED`; this describes the wording
interpretation, not verified certification. A reviewed restricted variant
remains a possible family for type-wide presentation. Always retain the raw
wording and evaluate approval currency separately. Missing mappings are missing
evidence, not evidence that the company cannot maintain a type.

`kind` supports `AIRCRAFT`, `ENGINE`, `COMPONENT`, `SPECIALIST`, `SERVICE`,
`OTHER` and `ALL`. Capability lists default to `AIRCRAFT`; directory and coverage
default to `ALL`. `limit` is 1–500. Directory and capability lists paginate by
stable increasing ID using the returned opaque `meta.next_cursor` (null ends
the list). Every response includes `meta.dataset_revision`; a consumer must
restart a multi-page read if this changes. It fingerprints catalogue content,
including mapping withdrawals, rather than the fetch time. Capabilities and
coverage also return `meta.generated_at`. Missing/inactive companies return 404.

Coverage returns `data.totals`, `countries`, `sources`, `unlocated` and
`definitions`. `geography=site_country` uses only the recorded physical-site
country; `company_country` explicitly shows company domicile instead. Countries
with no imported scope are absent; this never establishes national completeness.
Unlocated includes records missing the chosen geography. Source and authority
filters apply to counts as well as country rows. Country rows include
`source_codes` and `authority_codes`; source rows separate import, fetch,
source-snapshot and approval-verification dates. A missing source-snapshot date
stays null even when the data was recently imported or verified.

Each count object contains distinct companies, approvals, current approvals,
sites, capability rows, aircraft capability rows, site/company capability rows,
distinct interpreted type codes, mapped/unmapped aircraft rows, coordinate sites
and airport-linked sites. Family interpretations count as mapped, with their
restrictions retained in the detail response. Distinct counts cannot be added
across countries or sources. Current status, mapping coverage and freshness are
separate signals; none confirms the tooling, check privileges or availability
for a particular maintenance job.

The authenticated Heligent Tools UI uses equivalent viewer-protected GET paths:
`/api/tools/maintenance/companies`,
`/api/tools/maintenance/companies/{id}/capabilities` and
`/api/tools/maintenance/coverage`. These are unavailable in public-demo mode.

Run focused boundary and isolated PostgreSQL checks with:

```powershell
$env:PYTHONPATH = 'C:\path\to\heligent\src'
$env:HELIGENT_TEST_DATABASE_URL = 'postgresql://postgres@127.0.0.1:55432/heligent_test'
python -m unittest discover -s tests -p test_maintenance_catalog.py -v
```

Integration tests require a dedicated local database with `test` in its name.
They create and remove their own randomized schema, and never use production
tables. No schema migration or reimport is required for these endpoints.

## Categories and regions

Categories are `ALL`, `ROTORCRAFT`, `FIXED_WING`, `GLIDER`, `BALLOON`, `UAV`,
`OTHER` and `UNKNOWN`. Regional operations accept Africa, Asia, Europe, North
America, South America, Oceania and the Americas, with spaces, hyphens or
underscores normalized where appropriate. Regional activity is based on linked
airport geography, not the registered operator's home country.

## Interpretation boundary

ADS-B-derived hours and movements are observational estimates. They can drive
market intelligence, comparison and maintenance forecasting, but they must not
be represented as certified logbook hours/cycles or regulatory compliance
records. A missing date or tail is never silently converted to zero.
