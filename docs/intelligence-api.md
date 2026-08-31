# Aviation-intelligence API v1

This is Heligent's read-only product boundary for Sproutt and future clients.
It exposes stable aviation facts and supported rankings, not database tables,
raw ADS-B files, arbitrary SQL, live positions, tracks or confirmed flight
legs.

## Transport and authentication

The service binds to `127.0.0.1:5100`. In production it is published only with
Tailscale Serve. Every `/api/v1/*` request requires:

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
- estimated distance, airport-link count and candidate movements;
- airport identities and evidence; and
- `data_revision`, which changes when the dataset, identity or resolved
  operator cache is refreshed.

Use `(source, utc_date, address)` as the consumer-side fact key. Always inspect
`meta.coverage` and `meta.registrations_without_activity`.

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
