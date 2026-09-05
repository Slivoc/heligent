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
per section and explicitly signals truncation. It is an airport/flight timeline,
not a replay of individual ADS-B positions or a hangar-level map.

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
The rebuilt frontend is included. No Pi update or additional raw reprocessing is
needed beyond the existing flight-visits-v1 rebuild. Select a watched tail after
the seven-day parser pilot; verify MRO companies have active airport-linked sites.
