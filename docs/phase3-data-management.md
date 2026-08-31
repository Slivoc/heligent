# Phase 3: data-management application

## Delivered workflow

Phase 3 turns the ingestion command into a local operations surface. An
administrator can:

- select one completed historical UTC date and queue it;
- queue only missing dates across a range of up to 31 days;
- choose the public ADSB.lol download or the private Raspberry Pi archive API
  for each queue operation;
- see processed, active, failed, and not-local dates in a monthly calendar;
- monitor the current download/processing stage and persisted heartbeat text;
- inspect release, raw-size, aircraft, observation, storage, cleanup, and error
  details for a selected date;
- safely retry failed work or explicitly reprocess a completed date;
- cancel work that has not yet started;
- keep raw files when parser investigation justifies the disk cost.

The interface is available at `http://127.0.0.1:5080` after running:

```powershell
$env:DATABASE_URL = "postgresql://user:password@localhost/adsb_analytics"
adsb-web
```

## Queue and worker contract

`ingestion_queue` records user intent separately from `ingestion_job`, which
continues to hold the DOWNLOAD and PROCESS/REPROCESS stage history. A partial
unique index permits only one queued/running request for a date.

Each queue item persists its `raw_source` (`DIRECT` or `PI`), so restarts and
retries keep using the source selected by the administrator. `DIRECT` remains
the default. The worker does not silently fall back between sources: a missing
Pi archive or an unreachable Pi fails visibly and can then be retried from the
other source.

The worker claims the oldest item with `FOR UPDATE SKIP LOCKED`. A PostgreSQL
advisory lock permits only one sequential worker even if two web processes are
started. After a server restart, the lock owner requeues an interrupted queue
item; the existing ingestion reconciliation then marks stale stage jobs before
retrying safely.

Date replacement remains atomic. During reprocessing, the previously committed
tail/day and hub/day rows remain available until the replacement transaction
commits. A failed replacement does not duplicate or partially replace them.

## Raw download sources

The direct option retains the existing GitHub/ADSB.lol release discovery and
download path. The Raspberry Pi option reads a completed-day manifest from the
read-only archive API, then uses the same resumable downloader and SHA-256
verification as the direct path.

The Pi option appears as configured after both values are present in the main
application environment:

```text
ADSB_ARCHIVE_API_URL=https://your-pi.tailnet-name.ts.net
ADSB_ARCHIVE_API_TOKEN=the-same-secret-as-the-pi
```

The API URL should be a private Tailscale Serve address. The Pi API binds to
loopback and requires a bearer token; the local administration application
still binds to loopback and should not be exposed through the archive API.

## Web/API boundary

The Python application serves the compiled responsive SPA and same-origin JSON
API through Waitress. Mutation endpoints require a custom local-admin request
header, and the service binds only to loopback by default. This is an internal
prototype control plane, not an authenticated internet-facing application.

The Sites-compatible frontend source lives in `web/`; its production SPA build
is packaged under `src/adsb_ingest/web_static/`. Remote hosting is intentionally
not enabled because a hosted browser application cannot safely reach or launch
the workstation-bound PostgreSQL ingestion worker. A future hosted deployment
needs an authenticated, network-accessible backend rather than exposing this
local admin process.

## API summary

- `GET /api/overview?month=YYYY-MM`
- `GET /api/datasets/YYYY-MM-DD`
- `POST /api/queue/date`
- `POST /api/queue/range`
- `POST /api/datasets/YYYY-MM-DD/retry`
- `POST /api/datasets/YYYY-MM-DD/reprocess`
- `POST /api/queue/{id}/cancel`

## Validation

Python tests cover API date validation, the mutation guard, JSON serialization,
queue duplicate prevention, sequential claiming, cancellation, safe processed
date skipping, source persistence and validation, Pi API authentication and
range downloads, date replacement, and failure rollback. Frontend tests render
the Sites build, validate the Flask-served SPA bundle, and prevent starter
preview metadata from returning.
