# ADS-B Historical Analytics Prototype — Build Brief

## 1. Objective

Build a small internal web application that can:

1. Download a selected day of historical ADS-B data from ADSB.lol.
2. Parse the raw archive into a much smaller, queryable local database.
3. Track exactly which dates have been downloaded and processed.
4. Delete raw downloaded files once parsing has completed successfully.
5. Provide a simple frontend for checking that the data pipeline is working.
6. Show useful predefined tables and charts for selected snapshot periods.
7. Gracefully handle dates or periods where data has not yet been downloaded.
8. Provide a foundation for a later natural-language analytics interface using the ChatGPT/OpenAI API.

This is **not yet intended to be a polished commercial product**. It is a working prototype that can be shown to colleagues so we can explore which aviation analytics are genuinely useful before investing heavily in the final schema, UI or commercial model.

---

## 2. Important Product Principle

Do not try to solve every ADS-B analytics problem in version 1.

The immediate goal is:

> **Prove that we can reliably turn enormous daily ADS-B archives into manageable structured data, retain a record of what has been processed, discard the raw data, and then query/visualise the resulting dataset.**

The application should therefore favour:

- simple architecture;
- transparent processing status;
- easily inspectable data;
- low storage use after parsing;
- easy experimentation;
- extensibility later.

Do not spend significant time creating a beautiful public-facing application yet.

---

## 3. Source Data

Initial source:

**ADSB.lol historical archive**

Documentation:

- https://www.adsb.lol/docs/open-data/historical/
- https://github.com/adsblol/globe_history

At the time this brief was written, ADSB.lol states that historical data is published daily through GitHub releases and contains a gzipped JSON file for each aircraft for that day.

### Licensing

ADSB.lol states that its public historical data is licensed under **ODbL 1.0**.

The prototype can proceed, but **commercial use/distribution must not be launched until the licensing implications have been reviewed properly**.

The application should keep data-source attribution configurable so it can later be displayed where required.

---

## 4. Suggested Technical Stack

Use a pragmatic stack rather than introducing unnecessary complexity.

Suggested:

- **Python**
- **Flask**
- **PostgreSQL** initially
- **SQLAlchemy** if appropriate
- **Bootstrap** for UI
- **Chart.js** for charts
- background processing via a simple worker/process rather than blocking an HTTP request

If ingestion performance proves that PostgreSQL is the wrong tool for the volume, the architecture should make it possible to move the analytical/event data to **ClickHouse** later.

Do **not** begin with ClickHouse unless there is a concrete need. The prototype should establish the shape and usefulness of the derived dataset first.

---

# 5. Application Structure

For the prototype, administration and analytics can exist on the same page/application.

A simple navigation structure could be:

- **Dashboard**
- **Data / Downloads**
- **Explorer**

They can even initially be sections of one page.

The important distinction is conceptual rather than visual.

---

# 6. Data Download / Processing Workflow

## 6.1 User selects a date

The user should be able to select a historical UTC date.

Example:

`2026-08-20`

The UI should immediately show its known status:

- Not downloaded
- Download queued
- Downloading
- Download failed
- Download complete / processing pending
- Processing
- Processing failed
- Processed successfully
- Raw files deleted

Where possible also show:

- source release discovered;
- download start time;
- download completion time;
- bytes downloaded;
- processing start/end time;
- aircraft files encountered;
- records/positions encountered;
- derived records written;
- raw disk space deleted;
- error message if failed.

---

## 6.2 Download

The server should locate the appropriate ADSB.lol GitHub release/assets for the selected day and download the required source data.

Important requirements:

- stream downloads to disk;
- do not load entire archives into RAM;
- support large files;
- log progress where practical;
- safe retry after network failure;
- avoid accidentally downloading the same date twice;
- make download idempotent.

If ADSB.lol publishes multiple daily source-instance releases, initially either:

1. support all required instances and merge/deduplicate them; or
2. deliberately choose one documented source and clearly record which source was processed.

Whichever approach is chosen must be explicit in the database.

Do not silently mix datasets without recording provenance.

---

## 6.3 Parse incrementally

Processing must be streaming/incremental.

Avoid:

`download everything -> decompress everything -> load everything into memory`

Prefer:

`download -> iterate files -> decompress one stream/file -> parse -> aggregate/write -> release memory -> continue`

The parser should capture enough source information to allow useful experimentation without trying to preserve every raw ADS-B field forever.

---

## 6.4 Delete raw data

Once parsing has completed successfully and the derived database transaction/state is confirmed:

- delete the raw downloaded archive/files;
- record that deletion succeeded;
- retain metadata about the original download.

Do **not** delete source files after a partial or failed parse.

Provide an optional developer/admin setting:

`Keep raw files after processing`

Default: **off**.

This is mainly for debugging the parser.

---

# 7. Dataset Tracking

Create a first-class table for ingestion state.

Suggested table:

## `dataset_day`

Possible fields:

```text
id
utc_date
source
source_release_id
source_url
status
queued_at
download_started_at
download_finished_at
processing_started_at
processing_finished_at
raw_bytes
raw_file_count
source_aircraft_count
source_record_count
derived_record_count
raw_deleted_at
error_stage
error_message
created_at
updated_at
```

`utc_date + source` should be unique.

### Status values

Suggested enum/state values:

```text
NOT_DOWNLOADED
QUEUED
DOWNLOADING
DOWNLOADED
PROCESSING
PROCESSED
FAILED_DOWNLOAD
FAILED_PROCESSING
```

Raw-file deletion can be represented separately via `raw_deleted_at`, because processing success and raw retention are different concepts.

---

# 8. What Data Should Version 1 Store?

Avoid trying to infer perfect commercial flights immediately.

For the initial prototype, build two levels of data:

1. **aircraft-day summary**
2. **simplified track/observation data where needed for future analysis**

The exact balance can be adjusted after inspecting real source files.

---

## 8.1 Aircraft identity table

### `aircraft`

Suggested fields:

```text
icao_hex
registration
callsign_last_seen
type_code
type_description
country
military_flag
first_seen_date
last_seen_date
```

Aircraft metadata can be incomplete. Nulls are expected.

Do not assume an ICAO hex -> registration/operator relationship is permanently stable.

Design so historical identity enrichment can be added later.

---

## 8.2 Aircraft daily summary

### `aircraft_day`

One row per aircraft per processed UTC day.

Suggested fields:

```text
utc_date
icao_hex
registration
type_code
callsign_sample
first_seen_at
last_seen_at
observation_count
min_altitude
max_altitude
max_ground_speed
first_lat
first_lon
last_lat
last_lon
time_observed_seconds
estimated_distance_nm
```

Some metrics may initially be approximate.

The purpose is to give us an extremely cheap layer for initial queries such as:

- unique aircraft per day;
- most active aircraft;
- type distribution;
- registrations seen;
- number of observations;
- approximate utilisation/activity trends.

---

## 8.3 Optional retained position layer

We may later discover that airport detection and flight segmentation require more positional detail.

If retaining positions, **do not automatically store every source message forever without testing the storage impact**.

Potential options:

- retain only changed positions;
- sample every N seconds;
- retain significant state changes;
- retain only the derived track needed to reconstruct movements;
- store compressed track objects rather than individual rows.

This should be decided after parsing a few real dates.

The prototype should report the difference between:

- raw bytes downloaded;
- derived database storage added.

This ratio is an important commercial/technical metric.

---

# 9. Future Derived Tables — Do Not Overbuild Yet

The architecture should make it easy to add these later:

```text
flight
airport_movement
airport_day
operator
operator_day
aircraft_operator_history
airport
heliport
```

But version 1 does **not** need perfect flight detection.

The first milestone is proving ingestion, compression/derivation and analytics.

---

# 10. Dashboard

The dashboard should immediately answer:

> Is the ingestion pipeline working, and what data is currently available?

Suggested cards:

### Dataset

- Processed days
- Earliest processed date
- Latest processed date
- Missing days between earliest/latest
- Total aircraft-day rows

### Last ingestion

- Date
- Status
- Download size
- Aircraft count
- Processing duration
- Derived records
- Raw deleted: Yes/No

### Storage

- Raw download size for most recent processed day
- Derived storage estimate
- Compression/reduction ratio

Do not worry if some storage metrics are approximate initially.

---

# 11. Download Calendar / Data Availability View

This is important.

Provide a visual way of knowing which days exist locally.

A calendar or simple monthly grid is ideal.

For each date use states such as:

- Green: processed
- Blue: queued/downloading/processing
- Red: failed
- Grey: not downloaded

Clicking a day should show:

- status;
- statistics;
- error if any;
- Download/Retry button;
- Reprocess button where appropriate.

Also provide:

### Download date range

Example:

```text
From: 2026-08-01
To:   2026-08-20
[Queue Missing Days]
```

This should only queue dates not already successfully processed unless the user explicitly selects reprocess.

Jobs should process sequentially initially to keep bandwidth, RAM and disk behaviour predictable.

Parallelism can be added later.

---

# 12. Predefined Snapshot Period

The Explorer should have a selected period.

Examples:

- Yesterday
- Last 7 days
- Last 30 days
- Custom date range

However, these labels must refer to **local processed data**, not imply that missing source dates have magically been fetched.

---

## 12.1 Graceful Missing-Data Behaviour

This is a critical requirement.

Suppose the user chooses:

`Last 30 days`

but only 8 of those dates have been downloaded.

Do **not** show a misleading chart as though it represents 30 complete days.

Display something like:

> Data available for 8 of 30 days in this period.

Then provide:

`[Download missing 22 days]`

The chart/table may still show the available data, but it must be clearly labelled **partial coverage**.

If **zero** selected days have been downloaded:

Show an empty state rather than an error:

> No local data is available for this period yet.
>
> Download the required historical dates to populate this view.

Include a button:

`Download this period`

The application should never throw an ugly SQL/null/frontend exception simply because data is unavailable.

---

# 13. Initial Explorer / Proof-of-Life Analytics

Do not try to discover every useful aviation metric yet.

The goal is simply to prove that real ADS-B information has made it from archive -> parser -> database -> frontend.

For the selected available date range, provide a few tables/charts.

---

## 13.1 Daily unique aircraft

Chart.js line chart:

```text
Date -> unique ICAO aircraft observed
```

This is useful for immediately spotting whether a day looks incomplete or abnormal.

---

## 13.2 Daily observation volume

Chart.js line/bar chart:

```text
Date -> processed observations
```

Again, primarily a pipeline-health indicator initially.

---

## 13.3 Aircraft type distribution

Table + Chart.js bar chart.

Example:

```text
Type     Aircraft
A320     3,221
B738     2,990
H145       146
S92         72
...
```

Null/unknown types should be grouped as Unknown rather than excluded silently.

---

## 13.4 Most observed aircraft

Table:

```text
Registration | ICAO | Type | Days seen | Observations | First seen | Last seen
```

This is mainly a sanity check but may already reveal interesting behaviour.

---

## 13.5 Helicopter snapshot

Because helicopter analytics are an immediate use case, provide an initial helicopter-only table if aircraft type/description identification allows it reliably enough.

Example:

```text
Type | Unique aircraft | Total observations | Days observed
```

Potential types could include H145, H175, S92, AW139 etc.

Do not hard-code an incomplete helicopter list deep inside business logic.

Use a configurable aircraft classification/enrichment mechanism so it can improve later.

If rotorcraft classification cannot yet be made reliably, label the feature experimental.

---

# 14. Filtering

Keep initial filters simple:

- date range;
- registration;
- ICAO hex;
- callsign;
- aircraft type;
- helicopter only / all aircraft where possible.

The app does not need an advanced visual query builder.

---

# 15. Natural-Language Query Interface — Intended Demo Direction

This is not necessarily required for the first ingestion milestone, but the application should be designed with it in mind.

Ultimately the **demo/user view should be capable of being almost entirely a single text box**.

Example:

> Show me daily H175 activity for the last 30 days.

or:

> Which helicopter types were most active last week?

or eventually:

> Which European helicopter bases have grown fastest compared with the same period last year?

The intended flow is:

```text
User question
    ↓
OpenAI/ChatGPT API
    ↓
Structured query intent / SQL
    ↓
SQL validation
    ↓
Read-only database query
    ↓
Result dataset
    ↓
LLM/UI chooses suitable presentation
    ↓
Generated table + Chart.js chart + short explanation
```

---

## 15.1 Important security constraint

Do not give the LLM unrestricted database access.

At minimum:

- database user must be read-only;
- allow SELECT only;
- reject multiple SQL statements;
- block DDL/DML;
- impose row limits;
- impose query timeout;
- expose curated analytics views rather than sensitive/admin tables;
- log generated SQL;
- show a friendly message when a query cannot be answered with available data.

A better long-term design may be:

```text
Natural language -> JSON analytics specification -> server generates SQL
```

rather than trusting arbitrary model-generated SQL.

However, straightforward text-to-SQL is acceptable for an internal prototype if it is thoroughly restricted.

---

# 16. Automatic Chart Generation

Eventually a natural-language query should be able to return:

```json
{
  "title": "H175 activity by day",
  "description": "Unique H175 aircraft observed",
  "columns": [...],
  "rows": [...],
  "chart": {
    "type": "line",
    "x": "date",
    "y": "unique_aircraft"
  }
}
```

The frontend can convert the returned chart specification into Chart.js.

Supported initial chart types:

- line;
- bar;
- pie/doughnut only where genuinely appropriate.

Do not allow the LLM to generate arbitrary JavaScript or HTML.

It should generate a constrained chart specification that the application renders safely.

---

# 17. Missing Data in Natural-Language Queries

The query layer must understand dataset coverage.

Example question:

> Show me H145 activity in July 2026.

If only 4 July dates exist locally, the result should say something such as:

> Only 4 of 31 requested days are available locally. Results below are partial.

Potential buttons/actions:

- Download missing dates
- Continue with available data

Never silently return a partial result as though it represents the entire requested period.

---

# 18. API / Backend Endpoints

Exact routes are flexible, but something like the following would be sensible.

## Dataset administration

```text
GET  /api/datasets
GET  /api/datasets/<date>
POST /api/datasets/<date>/download
POST /api/datasets/<date>/retry
POST /api/datasets/<date>/reprocess
POST /api/datasets/range/download
GET  /api/jobs
GET  /api/jobs/<id>
```

## Analytics

```text
GET /api/analytics/overview
GET /api/analytics/daily-activity
GET /api/analytics/types
GET /api/analytics/aircraft
GET /api/analytics/helicopters
```

## Future natural-language endpoint

```text
POST /api/query
```

Example request:

```json
{
  "question": "Show me H175 activity over the last 30 days"
}
```

Example response:

```json
{
  "coverage": {
    "requested_days": 30,
    "available_days": 18,
    "complete": false
  },
  "answer": "H175 activity is available for 18 of the requested 30 days.",
  "table": {
    "columns": ["date", "unique_aircraft"],
    "rows": []
  },
  "chart": {
    "type": "line",
    "x": "date",
    "y": "unique_aircraft"
  }
}
```

---

# 19. Background Job Behaviour

Downloads and parsing must not block the Flask web process/request.

A simple job table is sufficient initially.

## `ingestion_job`

```text
id
dataset_day_id
job_type
status
progress_percent
status_message
started_at
finished_at
error_message
```

The frontend can poll job status every few seconds.

No need for WebSockets unless convenient.

The first implementation can process **one ingestion job at a time**.

This is safer for disk space and bandwidth while the resource requirements are unknown.

---

# 20. Logging

Create useful application logs for ingestion.

Example:

```text
[2026-08-20] Download started
[2026-08-20] 12.4 GB downloaded
[2026-08-20] Processing aircraft 18,240 / 41,991
[2026-08-20] Parsed 192,331,822 observations
[2026-08-20] Wrote 41,783 aircraft_day records
[2026-08-20] Raw files deleted
[2026-08-20] COMPLETE
```

Do not write a log line for every ADS-B position.

---

# 21. Failure Recovery

The prototype must handle interruptions reasonably well.

Examples:

### Download interrupted

- mark dataset as FAILED_DOWNLOAD;
- preserve useful error information;
- Retry should restart/resume safely where feasible.

### Processing fails

- do not mark date processed;
- do not delete source file;
- rollback or clear incomplete derived rows for that date;
- Retry/Reprocess must not duplicate records.

### Application/server restarts

On startup, detect jobs left in DOWNLOADING or PROCESSING unexpectedly and mark/reconcile them appropriately rather than leaving them permanently running.

---

# 22. Database Idempotency

This matters from the start.

Reprocessing the same date must not produce duplicate data.

Simplest safe approach:

```text
BEGIN
DELETE derived rows WHERE utc_date = selected_date
parse/write selected date
COMMIT
```

or use proper upserts/partition replacement.

If parsing fails, the database must remain in a known state.

---

# 23. Performance / Resource Instrumentation

Because we do not yet know the real economics, record basic metrics for every processed day:

- source GB;
- download duration;
- average download Mbps;
- processing duration;
- peak disk use if practical;
- source file count;
- source observation count;
- unique aircraft;
- derived rows;
- resulting DB storage increase if measurable.

This information is part of the experiment.

It will answer questions such as:

> Can a cheap VPS process one global day comfortably?

> How much storage does one year of derived data actually require?

> Can the nightly daily update finish comfortably before the next day's data arrives?

---

# 24. Initial UI Sketch

A single Bootstrap page is acceptable.

## Top

```text
ADS-B Analytics Prototype

Processed through: 20 Aug 2026
Available days: 17
Missing days: 4
Current job: None
```

## Data management panel

```text
[Date picker] [Download Date]

[From] [To] [Queue Missing Days]

Calendar / dataset status grid
```

## Snapshot controls

```text
Period: [Yesterday | 7 Days | 30 Days | Custom]
Aircraft Type: [All]
[ ] Helicopters only
```

Coverage indicator:

```text
18 / 30 requested days available
[Download Missing 12 Days]
```

## Results

- daily activity Chart.js chart;
- type distribution chart;
- top aircraft table;
- helicopter activity table.

## Future query box

Keep a visible placeholder even before it is functional:

```text
Ask the data

[ Which helicopter types were most active last week?                 ]
[ Ask ]
```

Initially it may display:

> Natural-language analytics coming next.

Once implemented, this should become the main demonstration interface.

---

# 25. Design for the Future Demo

Although the prototype has admin controls on the same page, structure the frontend so that later we can have:

### Admin/data-maintenance view

Invisible to normal demo users.

### Clean demo view

Almost entirely:

```text
┌──────────────────────────────────────────────────────────────┐
│ Ask anything about the available ADS-B history              │
│                                                             │
│ Which helicopter types increased activity most this month?  │
└──────────────────────────────────────────────────────────────┘

                         [Ask]
```

Then dynamically generate:

- a short answer;
- coverage warning if needed;
- table;
- one or more Chart.js visualisations;
- optional follow-up suggestions.

The eventual experience should feel like **conversation with an aviation database**, not like a traditional BI dashboard.

---

# 26. Things Explicitly Out of Scope for Initial Build

Do not spend time on these yet:

- subscriptions;
- payments;
- customer accounts;
- polished permissions system;
- mobile app;
- perfect airport inference;
- global heliport database;
- perfect flight segmentation;
- maintenance-event detection;
- aircraft ownership history;
- commercial operator enrichment;
- complex mapping;
- alerts;
- scheduled customer reports;
- multi-tenant architecture;
- public API product;
- advanced caching;
- AI agent workflows beyond simple analytics querying.

The architecture should not deliberately prevent these, but they are not version-1 requirements.

---

# 27. Suggested Implementation Phases

## Phase 1 — Inspect one real archive

- programmatically locate one ADSB.lol historical date;
- download it;
- inspect actual release/assets and JSON structure;
- document file sizes and fields;
- parse a small sample;
- decide the minimum useful retained schema.

**Deliverable:** command/script proves source can be downloaded and read.

---

## Phase 2 — One-day end-to-end ingestion

- `dataset_day` table;
- download one date;
- parse it;
- populate `aircraft` / `aircraft_day`;
- report metrics;
- delete raw data;
- rerunning is safe.

**Deliverable:** one date moves from NOT_DOWNLOADED -> PROCESSED.

---

## Phase 3 — Data management UI

- date selector;
- download button;
- status/progress;
- processed-day calendar;
- range queue;
- retries;
- missing-date awareness.

**Deliverable:** user can populate historical days without touching SSH/CLI.

---

## Phase 4 — Basic analytics UI

- snapshot period controls;
- coverage indicator;
- daily unique-aircraft chart;
- observation-volume chart;
- aircraft type table/chart;
- top aircraft table;
- basic helicopter view.

**Deliverable:** visually proves the dataset is real and queryable.

---

## Phase 5 — Natural-language prototype

- OpenAI API integration;
- curated database schema/views supplied to model;
- question -> safe SQL/analytics query;
- coverage validation;
- table response;
- constrained Chart.js specification;
- short textual summary.

**Deliverable:** user types a plain-English aviation question and receives a chart/table based on local ADS-B history.

---

## Phase 6 — Explore the actual product

Only after colleagues have used the prototype, decide which derived datasets are worth building.

Candidates include:

- flight segmentation;
- takeoff/landing detection;
- airport activity;
- busiest helicopter hubs;
- base growth rates;
- aircraft utilisation;
- fleet utilisation changes;
- operator activity;
- MRO visits;
- route discovery;
- unusual activity;
- aircraft type trends;
- regional comparisons.

Do not assume which of these has the highest value until users have interacted with the data.

---

# 28. Success Criteria for Prototype

The prototype is successful when all of the following are true:

1. I can select a historical date from the UI.
2. The application can download the ADSB.lol source data without manual intervention.
3. I can watch its ingestion state/progress.
4. It parses the source without holding the whole dataset in RAM.
5. Useful derived records appear in PostgreSQL.
6. The raw source data is deleted after successful processing.
7. The application permanently remembers that the date has been processed.
8. I can queue multiple missing historical dates.
9. I can see which days are available and which are missing.
10. Charts/tables work for downloaded periods.
11. Missing periods show a friendly coverage message rather than failing or misleading the user.
12. Reprocessing a date does not duplicate records.
13. I can demonstrate the application to colleagues and ask them what questions they would want the data to answer.
14. The architecture is ready for a natural-language -> analytics -> Chart.js interface without requiring a rewrite.

---

# 29. Development Philosophy

This is an **exploration tool first, product second**.

Prefer code that lets us learn what is possible with the dataset.

Do not prematurely optimise around assumptions such as:

- airport activity is definitely the product;
- helicopters are definitely the only market;
- every position must be retained;
- every query needs an LLM;
- PostgreSQL will definitely be sufficient forever;
- ClickHouse is definitely required now.

Build the ingestion foundation, expose enough data to play with it, then let real questions determine the next layer.

---

# 30. First Task for the Coding Agent

Start with **Phase 1 only**.

Do not immediately build the whole application.

For one known historical date:

1. Discover the actual ADSB.lol GitHub release/assets programmatically.
2. Report the release structure and file sizes.
3. Download enough of the source to understand its real format.
4. Inspect representative aircraft JSON/GZIP contents.
5. Identify which fields are consistently available.
6. Estimate records/day, aircraft/day and likely processing/storage requirements.
7. Propose the exact Phase-2 PostgreSQL schema based on the real data rather than assumptions in this document.
8. Preserve scripts/code so the inspection work becomes the beginning of the ingestion service.

After that evidence is available, proceed with the one-day end-to-end ingestion pipeline.
