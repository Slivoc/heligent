# Maintenance Pulse: team pilot

Phase 16 adds the Maintenance Pulse tab to the authenticated Heligent app.
Analysts can add registrations to the shared team watchlist, inspect the latest
90 days of processed flight and airport-visit evidence, review MRO stay
candidates, and record externally known maintenance dates. Unknown tails are
allowed; they remain visible even without observations.

Reviews require a maintenance/check type, a decision (confirmed, rejected or
uncertain), and evidence notes. Record the source of a confirmation. Airport
presence alone does not prove entry into an individual MRO or maintenance.
Manual dates use midnight UTC; candidate reviews retain precise inferred times.
Revisions preserve previous values in maintenance_event_revision. Removing a
watch archives it and retains reviews; adding the same tail reactivates it.
Raw-file reprocessing does not delete watches or reviews. Candidate evidence
is snapshotted when reviewed; reprocessing may generate a different candidate
key, so analysts should check existing events before reviewing a new candidate.

The page shows observed and elapsed airborne hours and inferred episode counts
since the most recent confirmed event, together with processed-day coverage.
Processed-day coverage does not guarantee receiver coverage for that aircraft.
Episodes spanning UTC boundaries are not certified flight/cycle counters.
Registration matching is currently historical registration text, not a durable
airframe identity across re-registrations. Check addresses when ownership or
registration changes are suspected. The timeline is limited to 500 entries
per section and explicitly signals truncation. For sampled flight playback and
Part-145 overlays, use the separate **Flight map** tab (see below). Neither page
establishes hangar-level presence.

Due-date prediction and automated alerts are deferred until reviewed comparable
maintenance events exist. Different check types must not be combined into one
maintenance interval. The current page computes results on demand with a
15-second database timeout; it is not a scheduled forecast job.

## Backend routes

These routes use the existing web session/proxy authentication, not the Sproutt
intelligence API bearer token. Mutations require ANALYST or ADMIN and the
`X-Requested-With: HeligentAdmin` header. Public demo access is blocked.

- GET /api/maintenance/watches: active internal watches and latest confirmed date.
- POST /api/maintenance/watches: registration and optional notes; idempotent add/reactivate.
- GET /api/maintenance/watches/{id}: watch, flights, visits, candidates, events and baseline totals.
- POST /api/maintenance/watches/{id}/reviews: started_at, ended_at (timezone required),
  maintenance_kind, status, notes; optional candidate_key or event_id for revision.
- POST /api/maintenance/watches/{id}/archive: retain history but remove active watch.

The watchlist table separates lists from members for future customer ownership.
Only scope_key=internal is accessible today; customer tenancy and list membership
authorization must be implemented before exposing customer-specific lists.

## Deployment

Commit/push locally, then use the VPS update procedure in vps-deployment.md.
Migration phase16.sql is included in heligent-migrate and application startup.
The rebuilt frontend is included. The phase-16 watchlist/review feature alone
needs no Pi update or additional raw reprocessing beyond flight-visits-v1.
The phase-17 map adds a migration and needs reprocessing for actual paths, as
described below. Verify MRO companies have active airport-linked sites.

## Flight map (phase 17)

Open **Flight map**, or `/#tracks`. Choose a team-watched registration and UTC
dates (default: latest processed week; maximum: 31 days). Select an episode to
play or scrub its sampled observations. Airport-visit markers show the times,
confidence and ground-observation count. The base sidebar supports search,
type-match filtering and individual selection where markers overlap.

Solid blue lines connect retained ADS-B samples; **dashed grey lines are only
inferred airport connections**, never actual flown paths. Flights without known
endpoints may have no legacy connection at all. Missing dates, receiver gaps,
unmapped bases and display limits are reported explicitly. Playback holds the
last observed position through gaps rather than animating an invented flight.

The map uses registrations and type codes recorded in `aircraft_day` on the
selected dates, not current ownership or a permanent airframe identity. This
can differ from registry-resolved identities in Explorer. Multiple addresses
are warned about; conflicting type codes disable highlighting. Start with a
known tail/date when validating a registry conflict. A processed day does not
guarantee reception for a particular aircraft.

### Base capability colours

- Green: active, site-specific AIRCRAFT capability with an exact matching ICAO
  type code and a VALID approval within its recorded validity dates.
- Amber: same type match, but only a company-wide capability; verify this base.
- Red: matching type recorded, but approval status/dates are not current.
- Grey: no recorded exact match (or aircraft type is unknown/conflicting).
  **This is not a finding that the base cannot service the aircraft.**

Only active companies/sites with a recorded Part-145 approval are included.
Suspended/expired records remain visible for context, not as current matches.
Capabilities belonging to another specific site are excluded. Company approval
associations and explicit site–approval links are distinguished in the panel.
The panel exposes capabilities, limitations, base/line flags, approval numbers,
validity, last verification and source links. Matching uses today's recorded
approval validity, **not** approval status at the historical flight date. No
fuzzy model matching or assumed variant/check/tooling/slot suitability is used.

Site coordinates are preferred; airport-centre fallbacks are labelled and must
not be read as a hangar location. Sites without coordinates stay in the list.
No automatic geocoding, external enrichment or maintenance confirmation occurs.

### Parser and storage changes

`phase17.sql` adds nullable `aircraft_flight_segment.track` JSONB and a historical
registration/date lookup index. `flight-visits-v2-tracks` keeps the existing
episode/visit calculations, adding compact airborne coordinates for **active
team-watched aircraft only** in the ingestion worker. The watchlist is snapshotted
once at the start of processing each date; its registrations and `track_scope`
are recorded in `dataset_day.derivation_config`. Adding a tail during a running
day does not change that day's snapshot. Newly watched tails require explicit
reprocessing of earlier dates to obtain paths. Other aircraft retain their
ordinary episode/visit summaries without the added coordinate-storage burden.
The standalone `summarize_trace` function defaults to retaining paths; ingestion
passes the watchlist decision explicitly for each trace.

The versioned object contains `segments` of `[epoch_seconds, latitude, longitude,
altitude_ft_or_null]`, input/retained counts, a truncation flag, sampling interval
and reception-gap threshold. Sampling retains one point per 15 seconds plus
continuous-run endpoints, rounded to five coordinate decimals. It is not an
error-bounded reconstruction: short turns between retained fixes may be lost.
Lines break on missing fixes, >120-second gaps (configurable continuous-gap
threshold), implausible speed jumps and antimeridian crossings. These checks
are quality safeguards, not a guarantee that every ADS-B fix is correct.
Ground taxi paths and precise hangar entry are not retained in this version.
The original archived files remain the source for finer-grained future work.

Each episode retains at most 2,048 points; later points are explicitly flagged
as omitted when capped. COPY batches also flush at 50,000 buffered track points
to limit the additional Python memory burden. Storage and parsing work will
increase; measure a representative day's derived size before a full rebuild.
Existing datasets remain untouched until **explicitly** reprocessed. Rebuilding
replaces derived paths/episodes for that day but preserves watchlists/reviews.
Only tails active in that rebuild's watchlist retain paths: archiving a tail
does not immediately delete its paths, but a later date rebuild can replace them
with NULL. Keep tails watched while rebuilding history you want to retain.

Map responses are bounded to 250 newest episodes, 500 newest visits and 30,000
track points; narrow dates when a limit is reported. The base catalogue caps at
5,000 sites / 20,000 capability rows with explicit warnings. Database statements
time out at 15 seconds. No map read downloads archives or runs processing jobs.

### Map API and CARTO configuration

- `GET /api/maintenance/watches/{id}/map?from=YYYY-MM-DD&to=YYYY-MM-DD`:
  watch identity, coverage, aircraft-days, flight tracks, visits and base scope.
- `GET /api/maintenance/map-config`: the browser-visible CARTO basemap key.

Both require normal Heligent web authentication; public demo access is blocked.
No API bearer credentials or server-only secrets are returned. CARTO's tile key
is intentionally browser-visible. Set `HELIGENT_CARTO_BASEMAP_KEY` in
`/etc/heligent/heligent.env` and restart `heligent-web`. Request a key for the
actual app domain and review the usage terms at
[CARTO's basemap setup](https://carto.com/basemaps/apikey/). Do not substitute a
general-purpose CARTO account secret. Keep the CARTO/OpenStreetMap attribution.
Browser tile requests expose the user's IP and viewed tile area to CARTO; tail,
capability and watchlist data are not included in tile URLs. No tile proxy or
bulk downloads are implemented. Without the key, overlays work on a blank base
with a setup message. The CSP permits only CARTO's tile host for external images.
The map uses locally bundled [Leaflet](https://leafletjs.com/reference) 1.9.4.

### Updating while backfill is running

Nothing has been changed on the VPS by implementing this locally. Prefer letting
the current backfill finish before deploying, or wait for a safe processing
boundary. Take/verify the normal backup and use the stop/pull/install/migrate/
start procedure in [vps-deployment.md](vps-deployment.md#deploy-an-update), **not a
live `git pull` while a worker is processing**. Phase 17 is included in migration
and startup. Restart **all three** services, including `heligent-ngrok`; stopping
the web service can also stop its dependent tunnel.

The compiled SPA assets are checked in; the VPS needs no Node build. The Pi needs
no changes and archived files need not be downloaded again from upstream if they
are still available on the Pi. After deployment, explicitly reprocess **one known
watched-tail day** using the Pi source (add the tail before processing starts), inspect its map and storage growth, then
decide which older dates to rebuild. Already completed v1 dates cannot acquire
real flight paths through migration alone. The ongoing queue is not automatically
requeued or rewritten by this change.

### Validation

Unit tests cover sampling endpoints, gaps, invalid positions, antimeridian breaks,
track caps, date validation, scope matching and authenticated routes. PostgreSQL
integration tests exercise persistence, legacy NULL tracks and site-scope isolation.
`web/tests/flight-map.browser.cjs` exercises the built SPA with synthetic API/tile
fixtures: selection, playback, base scopes, filtering, empty tails and mobile
layout. It needs Playwright available through Node resolution (it is not a
production dependency) and the built static directory served on loopback port
5089, or `MAP_TEST_URL` set to another local test server. It does not test a real
CARTO key or production receiver coverage.
