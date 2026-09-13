# tar1090-db source tool

Open **Tools → Sources & coverage → tar1090-db** (`#tools/source/TAR1090_DB`).
Analysts and admins can run this tool; viewers can read saved comparisons.

1. **Download tar1090 snapshot** retrieves the publisher's aircraft CSV and type
   reference. **Refresh tar1090 snapshot** checks again later. Downloads run on
   demand, with one refresh at a time and at least one minute between attempts.
2. Choose **Europe** and **Tail number**, then **Compare observed gaps**. Blank
   dates use the latest processed week; explicit periods can span up to 31 days.
3. Start in **Helicopters / rotorcraft** and click **Fill N identities**. This fills
   all eligible matches in the selected group/search, across every result page.
   Dates come from the comparison period and source notes are automatic.
4. **Add matched helicopters to Maintenance Pulse** is checked by default. Newly
   filled aircraft appear on the watchlist with their cached flight and visit
   identities updated immediately. Existing watch notes and reviews are preserved.
5. Watch the progress count; conflicts are skipped with a reason. You can stop
   after the current aircraft and resume later. Completed fills are retained and
   retries do not duplicate identities or watches. Run a new comparison to measure
   remaining gaps.

This is the quick path for internal testing: it provisionally uses the current
community identity for the selected observation period. It does not claim that
those are independently verified effective assignment dates. The original source,
assumption, previous values and actor remain recorded automatically.

**Review dates for HEX** retains the optional individual workflow for cases where
specific effective dates and evidence are available. No per-aircraft date entry or
confirmation is needed for the bulk path.

## What is retained

The adapter resolves the `csv` and `master` branches to Git commit hashes before
downloading their respective files. Aircraft and type references can have different
publication dates. Both immutable URLs, commit dates, SHA-256 hashes, compressed
files and field counts are stored in PostgreSQL. The source workspace displays
those details, latest saved comparison, refresh history, source notes and freshness.
Identical input and revision metadata reuse the existing snapshot. Failed or
interrupted attempts do not replace the last successful snapshot.

Freshness uses the aircraft CSV commit date. This measures publication of the
repository snapshot, not when each aircraft identity was verified. Owner/operator
and year columns remain in the retained original file; the adapter does not import
owner names as operators. Flags are retained as supplied, without deriving identity
or effective dates from them.

The input format follows the [publisher's CSV exporter](https://github.com/wiedehopf/tar1090-db/blob/master/toJson.py):
headerless, semicolon-delimited, backslash-escaped, eight columns including the
empty trailing field. The reference download is documented in the
[publisher repository](https://github.com/wiedehopf/tar1090-db). Attribution is
retained for tar1090-db/wiedehopf, Mictronics and ADSB Exchange. These are shared
upstreams, so agreement with a direct Mictronics lookup is not independent evidence.

## Matching and review boundaries

Only exact six-digit ICAO hex matches are proposed. A hex enters a regional
comparison through airport-visit evidence; all its processed, position-bearing
observations in the selected period are then checked, including complete days.
Geography is observed activity, not registration country. Aircraft without usable
airport geography can be inspected under **No airport region**.

The imported type reference supplies category evidence where the local type table
has no known category. Helicopter, gyrocopter and tilt-rotor descriptions are grouped
as rotorcraft. Ground identifiers and gliders are separated. Differences from
known local categories or resolved tails/types appear as conflicts. The source
comparison itself does not modify the global classification table or identities.

Confirmation rechecks raw records, current registration, existing overrides,
resolved observations and applicable national registry evidence. A changed preview,
conflicting identity, reused tail, or overlapping override blocks the assignment.
The client cannot substitute another tail or type for the saved source claim.
The optional individual review requires both effective dates. The bulk action uses
its saved comparison dates automatically. Ground identifiers are ineligible. The
existing dated-override path records previous values and the review, now including
the tar1090 snapshot ID, preview ID, revisions, hashes and claimed fields.

When approving a known rotorcraft or fixed-wing type, a missing/unknown local type
classification is filled with the reviewed source evidence. Existing known type
categories are preserved. This makes newly approved helicopter types usable by the
normal helicopter filters. The bulk action refreshes the affected aircraft's activity cache in the same
transaction, so Maintenance Pulse's flights, visits and candidates use the identity
immediately. Other aggregate caches and Sproutt reports still use their normal
refresh/sync. Trace activity is not reprocessed.

## Deployment and operation

Migration **phase23.sql** adds `tar1090_snapshot`, `tar1090_attempt` and
`tar1090_preview`. **phase24.sql** adds a fill-missing-only override mode for the
quick path: later reingestion keeps reported identities and only fills compatible
blank fields. Overlapping test periods with the same provisional identity merge
automatically; individual reviewed overrides keep their existing behavior. It runs through normal startup and `heligent-migrate`. Deploy
the Python source, schema files and rebuilt tracked SPA using the usual VPS update
process. No new API keys or packages are required. Run the first download from the
source workspace after deployment.

Original compressed files are retained in the database for reproducibility, rather
than expanding every 600,000-row snapshot into permanent aircraft rows. There is
currently no automatic pruning of source snapshots or saved comparisons; include
them in normal database backups and capacity monitoring. Refreshes and comparisons
are synchronous and may take about a minute. Network responses, expanded data,
record counts and comparison date ranges are bounded; malformed/truncated exports
and unexpectedly small downloads fail validation.

Validation commands:

```powershell
python -m unittest discover -s tests
# Only against an isolated disposable PostgreSQL database: integration setup
# drops and recreates the public schema.
$env:TEST_DATABASE_URL='postgresql://.../isolated_test_database'
$env:PYTHONPATH='tests'
python -m unittest test_tar1090 test_tar1090_integration -v
cd web
npm run lint
npm test
# In another terminal, from the repository root with TEST_DATABASE_URL set:
# python tests/serve_tar1090_fixture.py
# This replaces the public schema of a local heligent_tar1090_test_* database.
# With that server running and Playwright available:
node tests/tar1090-bulk.browser.cjs
# Restart the fixture server before testing the optional individual workflow:
node tests/tar1090.browser.cjs
```
