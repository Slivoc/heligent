# Maintenance Pulse: team pilot

Phase 16 adds the Maintenance Pulse tab to the authenticated Heligent app.
Analysts can add registrations to the shared team watchlist, inspect the latest
90 days of processed flight and airport-visit evidence, review MRO stay
candidates, and record externally known maintenance dates. Unknown tails are
allowed; they remain visible even without observations.

The watchlist shows each aircraft's operator, observed type, latest confirmed
maintenance date and last observed date. Search accepts registrations with or
without hyphens, operator names and type codes. Operator, aircraft type and
review-status filters work together; unknown operators and types have their own
options. Clear filters to return to the full list.

Use **Remove** on a watchlist row without opening the aircraft. **Undo removal**
restores the most recently removed watch, including its notes and reviews.
Removal archives the watch; it never deletes maintenance evidence. On a narrow
screen, each row becomes a compact aircraft card with the same actions.

Operators follow current recorded operator claims, using the same address-first,
then registration matching as Explorer. Type and last observed date come from
the latest processed aircraft-day matching the watched registration. These are
registration-based observations, not a permanent airframe identity. An operator
can be shown for an unobserved tail when a current registration assignment exists.

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
per section and explicitly signals truncation. For stop history and
Part-145 overlays, use the separate **Stops map** tab (see below). Neither page
establishes hangar-level presence.

Due-date prediction and automated alerts are deferred until reviewed comparable
maintenance events exist. Different check types must not be combined into one
maintenance interval. The current page computes results on demand with a
15-second database timeout; it is not a scheduled forecast job.

## Backend routes

These routes use the existing web session/proxy authentication, not the Sproutt
intelligence API bearer token. Mutations require ANALYST or ADMIN and the
`X-Requested-With: HeligentAdmin` header. Public demo access is blocked.

- GET /api/maintenance/watches: active internal watches, latest confirmed date,
  operator/source, latest observed type code and last observed date.
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
The phase-17 stops map adds only a lookup index. It uses existing visit summaries
without reprocessing or extra coordinate retention. Verify MRO companies have active airport-linked sites.

## Stops map (phase 17)

Open **Stops map**, or the existing `/#tracks` bookmark. Choose a watched
registration and UTC dates (default: latest processed week; maximum: 31 days).
The map reads existing airport-visit summaries, not individual ADS-B coordinates.

Numbered stops are chronological within the displayed records. Repeated visits
at one airport share a marker listing their numbers; select an individual record
from the timeline. Marker size reflects the largest **observed ground duration**
among that airport's displayed visits, not the span between sightings.
Dashed connectors show sighting order for the same address only. They are not
flight paths, proof of a direct journey, or evidence of continuous reception.

Selecting a stop shows arrival/departure estimates and evidence types, first/last
sightings, observed ground time, elapsed evidence span, confidence, quality flags
and open UTC boundaries. **A six-hour span with thirty minutes of observed ground
time is not six confirmed hours at the airport.** Proximity-only episodes are
explicitly marked as unconfirmed stops. Daily records remain separate; no
multi-day stay is inferred across silence.

The base sidebar defaults to airport-linked bases at the selected stop, with an
option to show the wider catalogue. Same-airport links are investigatory leads,
not proof that the aircraft entered that base. Search, type-match filters and
individual selection handle overlapping airport-centre markers.

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

### Storage and existing data

The earlier coordinate-retention proposal has been withdrawn. Parsing remains
`flight-visits-v1`: no intermediate coordinates, track JSON, playback or watchlist
snapshot are added to ingestion. The watchlist selects what is displayed, not
what ordinary airport visits are collected. Adding a tail can therefore reveal
its already-processed stop history immediately.

`phase17.sql` now creates only the historical registration/date lookup index.
There is no new track column on a fresh deployment. If an experimental version
was applied elsewhere, any existing track column/data is left untouched rather
than deleted automatically; the revised parser and map do not write or read it.

No extra raw processing or Pi update is required **if the selected dates already
have flight/airport-visit summaries**. Older dates processed before that existing
visit derivation will not gain visits from a UI update alone. Missing receiver
coverage or missing airport matches also cannot be fixed just by drawing a map.

The response contains the latest 500 daily visit records in the selected window,
then numbers them chronologically. A truncation warning identifies incomplete
history. The catalogue caps at 5,000 sites / 20,000 capability rows, with warnings.
Database statements time out at 15 seconds. No map read downloads files,
reprocesses a date, alters reviews or starts a job.

### Map API and CARTO configuration

- `GET /api/maintenance/watches/{id}/map?from=YYYY-MM-DD&to=YYYY-MM-DD`:
  watch identity, coverage, aircraft-days, numbered `stops` and base scope.
  Each stop separates `ground_time_seconds` from `evidence_span_seconds`.
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

The compiled SPA assets are checked in; the VPS needs no Node build. No Pi change
or new map-specific reprocessing is required. Commit and push this revision as a
normal follow-up commit, then pull the updated branch on the VPS; no force-push,
history rewrite or intermediate deployment of the earlier map is needed.

After deployment, select an already-processed period and a watched tail. Existing
airport visits should appear immediately. Nothing automatically requeues the
running or completed backfill.

### Timeline review aids

The Stops map timeline can be sorted chronologically, newest first, or by highest observed ground time. Sorting affects the displayed list only: chronological stop numbers and map connectors remain unchanged. Airport-linked maintenance sites appear as badges on each stop, showing existing match status (site-specific, company-wide, possible/restricted family, stale, expired or no recorded match). Unknown aircraft type is explicitly labelled as not evaluated. These use the loaded catalogue and current approval status, not proof of historical approval or actual maintenance. Incomplete/unlocated catalogue records cannot produce airport badges.

Hangar coordinates improve map placement but do not make airport-level visit summaries precise enough to prove hangar entry. Reliable facility attribution would additionally need suitable ground-position evidence and uncertainty handling; no such data retention is added here.

### Validation

Unit tests cover date limits, capability scope and authenticated endpoints.
PostgreSQL integration tests verify existing visit summaries appear without
a track column or raw rebuild, plus site-scope isolation and empty tails.
`web/tests/flight-map.browser.cjs` uses synthetic API and tile fixtures for stop
selection, observed-vs-elapsed evidence, repeat visits, catalogue filters, empty
tails and mobile layout. It needs Playwright through Node resolution and a
loopback static server on port 5089 (or `MAP_TEST_URL`). It is not a production
dependency and does not test a real CARTO key or live receiver coverage.
