# Phase 5: natural-language analytics

Phase 5 makes the local ADS-B history feel like a conversation rather than a
BI dashboard. It deliberately builds on the compact Phase 4 metrics: airport
hub traffic, tail activity and airborne hours, aircraft types, and explicitly
classified rotorcraft. It does not introduce journey reconstruction.

## Runtime configuration

The OpenAI API key belongs only in the Flask server environment:

```powershell
$env:OPENAI_API_KEY = "your-api-key"
$env:OPENAI_MODEL = "gpt-5.4-mini" # optional; this is the default
$env:DATABASE_URL = "postgresql://user:password@localhost/adsb_analytics"
adsb-web
```

The interface reports a friendly setup notice when the key is absent. It never
asks for or stores a key in the browser.

## Request flow

1. The browser posts the current question and at most four recent questions.
2. The server supplies the model with local date availability and a compact
   semantic schema made only from `nl_*` aviation views.
3. The OpenAI Responses API returns strict structured output containing one
   PostgreSQL `SELECT`, result metadata, dates, and an optional chart
   specification. API response storage is disabled.
4. The server parses the SQL into an AST, rejects every relation outside the
   semantic allowlist, rejects unsafe functions and locking or multi-statement
   SQL, verifies the activity dates, and forces an outer limit of 100 rows.
5. PostgreSQL explains the query before execution. Queries over the cost cap
   are rejected; accepted queries run in a read-only transaction with lock and
   statement timeouts. One model repair is allowed after a validation or
   execution rejection.
6. The server creates the coverage warning and short answer from the validated
   plan and returned rows. The same row/column contract drives the full app and
   mobile demo.

The model receives the question, recent question text, semantic schema, and
available dates. It does not receive the database URL, raw ADS-B rows,
individual-tail results, or the final query result.

## Semantic relations

| Relation | Purpose | Example fields |
| --- | --- | --- |
| `nl_aircraft_activity` | One observed aircraft per UTC day | tail identity, category, operator evidence, active/airborne hours |
| `nl_airport_activity` | One aircraft/day/linked airport | airport geography, ground evidence, movement candidates |
| `nl_region_activity` | One aircraft/day/world activity region | region-linked tails and non-duplicated whole-day aircraft hours |
| `nl_country_activity` | One aircraft/day/activity country | country-linked tails, airports and movement candidates |
| `nl_aircraft_assignment` | Current operator assignments | tail, operator, source, confidence |
| `nl_company` and `nl_company_site` | Imported operator/MRO directory | roles, region, sites, tracked counts |
| `nl_approval` and `nl_capability` | Regulatory maintenance scope | authority, approval, model, capability, site |

`nl_aircraft_activity` is backed by an indexed semantic cache because resolving
registry identity over the entire history is too expensive for an interactive
request. Region and country evidence has a separate indexed aircraft-day cache,
so geographic queries do not regroup all airport links interactively. Ingestion
adds only the newly processed dataset day. Registry imports refresh only
aircraft touched by that batch, while readers keep seeing the previous committed
snapshot until the refresh commits.

World-region activity must filter `activity_region`, not
`operator_home_region`. A request to show an operator “if available” selects the
nullable operator column without filtering unknown operators. Explicit calendar
months remain full calendar months even when local coverage is partial.

Chart output is server-controlled: time series may be line or bar charts;
rankings are bar charts; a doughnut is allowed only for small aircraft-type
breakdowns. `table` suppresses the chart. No JavaScript or HTML is accepted
from the model.

Questions such as “which aircraft arrived and departed ABZ?” route to
`airport_aircraft`, with `ABZ` resolved against the local airport reference.
The retained summaries identify aircraft through explicit ground observations
and conservative low/slow trace endpoints. They retain aggregate movement
candidate counts, not journey segments, so answers label arrivals and
departures as ADS-B-derived candidates rather than certified movements.

## Coverage contract

Every answer includes:

```json
{
  "coverage": {
    "requested_days": 3,
    "available_days": 2,
    "complete": false,
    "available_dates": ["2025-08-21", "2026-08-20"],
    "missing_dates": ["2026-08-19"]
  }
}
```

The actual missing-date list is included in live responses. If only part of a
requested period exists locally, both the coverage panel and the textual answer
say that the result is partial. There is no silent substitution with a
different date range.

## HTTP API

- `GET /api/query/config` returns planner readiness, model name, available local
  dates, and date-aware example questions.
- `POST /api/query` accepts
  `{ "question": "...", "context": ["..."], "debug": false }`.

The POST endpoint uses the same local-interface request header protection as
the ingestion controls. Questions are capped at 600 characters and context at
four prior questions.

## Per-question debug dumps

Enable **Save debug details** beneath the question box before submitting a
question. The response shows the resulting filename under
`reports/query-debug/`; give that filename to Codex when a result needs
investigation.

Each JSON dump contains the question, local availability, raw structured model
plan, validated plan and SQL, query guard settings, planned cost, execution
time, returned rows, final response, and OpenAI response/request IDs when
available. It never contains `OPENAI_API_KEY`, the database URL, the raw model
prompt, or raw ADS-B positions. Debug files are ignored by Git because question
text and derived local data may still be sensitive.

“Whole database”, “entire history”, and equivalent wording use the sparse set
of locally available UTC dates rather than silently collapsing to the latest
day. For generic hub “traffic” or “busiest” questions, the server uses unique
observed aircraft. Ground observation counts are sampling-dependent, while
primary-aircraft counts answer the narrower question of which hub is primary
for a tail.

Company reference queries use no activity date range: they search imported
regulatory records rather than ADS-B history. A question such as “Which UK Part 145
MROs can maintain EC135 helicopters?” searches normalized fields and the raw
approval limitation text. It does not treat `EC135` as a traffic filter and it
does not imply that an approved organisation performed work, has current
capacity, or operated aircraft seen in Heligent. Operator/traffic analysis is
available only after a source supplies explicit aircraft assignments.

## Verification without a live API key

The unit suite injects a fake planner and executor and verifies strict
Structured Output request construction, SQL parsing and allowlists, date and
row bounds, repair behavior, coverage propagation, table/chart construction,
and the HTTP contract. PostgreSQL integration tests replay the migration and
exercise incremental registry refresh. This makes all safety paths testable
without sending data or incurring API usage. A real API key is needed only for
live natural-language planning.
