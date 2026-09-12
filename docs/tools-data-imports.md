# Tools / Data imports — first pass

The current entry point is **Tools → Sources & coverage**. See
[Sources and identity coverage](source-tools.md) for the source inventory,
freshness, region gap report and individual ADSBdb/HexDB cross-references.
The historical first-pass behavior below is superseded where that guide describes
resolved-category filtering and additional missing-type review modes.

## Hexes without tail numbers

Tools includes an unidentified-aircraft page. It ranks position-bearing daily records with NULL/blank registration by estimated airborne hours. The default is the latest processed week, with a maximum 31-day range and 50 hexes per page. It shows observed dates, positions, recorded types, up to 20 distinct callsigns, and the current aircraft registration separately. Totals include only the missing-registration days, not the aircraft's entire history. Records whose current registration is populated remain visible if historical daily metadata is missing. Callsigns are evidence for review, not automatic identity assignments. Failed/unprocessed dates are excluded and coverage is displayed.

### Assign a tail number

Trial workflow: source URL and notes are now optional. If a URL is supplied it must still be a valid HTTP(S) URL. Effective dates, preview, role checks, conflict protection and audit retention remain in place; an assignment is a working interpretation, not independently verified proof.

### Helicopter and region filters

The unidentified list defaults to **Helicopters** with **Include unknown types** checked, to avoid hiding untyped aircraft such as G-WSAS. Uncheck it to require a recorded rotorcraft classification. Other choices are unknown only, fixed wing only, or all. Classification comes from recorded ICAO type classification, not inferred from callsigns. Unknowns may include fixed wing. Strict helicopter results explicitly explain the exclusion and offer an **Include unknown types** action; an empty strict result does not establish that there are no unidentified helicopters. The API also defaults to `ROTORCRAFT_UNKNOWN`; explicit `ROTORCRAFT` remains strict and `ALL` remains available.

**Hex or callsign** searches the selected dates, region and category across all result pages. It is case-insensitive and ignores punctuation, so `G-WSAS`, `GWSAS` and `4082A2` find the relevant clue/address. Search selects whole hex groups: totals, callsigns and visits still include all their matching missing-registration days, including days with a different callsign. It never assigns an identity. Searches are limited to 40 characters and use the existing 15-second database query timeout.

A read-only production check on 11 September 2026 reproduced the empty strict Europe result for the latest processed week, 31 August–6 September: all 9,484 matching missing-registration aircraft-day records had unknown type classification. The updated Europe query with `search=G-WSAS` returned hex `4082a2`, six days, 7,963 positions and 10.43 estimated airborne hours, with visits at Glasgow, Aberdeen and Inverness. Both its registration and type remain blank. `GXSAS` independently returned `4082a1`. The candidate query was run in memory with `default_transaction_read_only=on`; no production files, records or services were changed. This fix needs the Python code and rebuilt SPA, with no schema migration or raw reprocessing.

Region means an observed airport visit in the selected continent or the UK, not country of registration. Aircraft-days without an airport visit in that region are excluded; use Worldwide to include unlocated activity. Hours are full daily estimates for matching days, not hours geographically contained inside the region. Each hex shows its top three recorded airport visits within the filters, with visit-record counts and observed ground hours. These are daily visit episodes, not necessarily separate landings or confirmed visits to an individual maintenance facility. No new raw processing or schema migration is needed for these filters.

Analysts/admins can select **Assign tail number**, enter the registration, optional ICAO type, verified effective start date, optional inclusive end date, evidence URL and notes. Preview shows all affected daily records in that effective interval, not just those on the current list page. Confirm saves a dated override and updates historical metadata through the existing identity trigger. Blank end date covers future ingestion. Flight/visit records and raw archives are not reprocessed.

Overlapping overrides, a different registration/type already in the interval, or the registration appearing under another hex in the interval block assignment. Changed underlying records invalidate the preview. This first version creates new non-overlapping corrections only; editing/reversing existing corrections requires administrator review. Do not bypass conflicts by choosing arbitrary dates.

Migration **phase19.sql** adds an audit table retaining the assignment, reviewer and previous historical/current identity values. It is registered with normal migration/startup. Deploy the compiled frontend too. No identity is pre-seeded. The Stops map reads corrected daily records immediately; existing analytics caches and Sproutt snapshots still need their normal refresh/sync. No whole-database cache rebuild runs inside the assignment request.

## Import a selected LBA organisation

Open **Tools → Data imports → Open LBA preview** (direct link `/#tools/lba`). Capability mappings also sits under Tools; its old direct link and Stops map review buttons still work.

Analysts/admins can fetch an organisation search such as ADAC. The server makes two bounded HTTPS requests to the fixed LBA directory, preserving organisation → approval → site → rating → model wording. Requests are limited to one search per 30 seconds per app process; response size is capped at 4 MiB. Unexpected layouts stop the preview. Pagination is displayed and incomplete results are flagged; this version never crawls the full directory.

Fetching now saves the parsed preview, source timestamp and response hash in PostgreSQL. The catalogue is unchanged until an analyst/admin chooses **Import this organisation → Confirm organisation import**. The request sends only the saved preview ID and selected result index; it cannot replace the source data with client-supplied content. Previews expire for import after 24 hours, but remain stored for audit. Raw HTML is not retained.

The first importer supports complete DE.145 organisation results, up to 250 sites / 2000 model capability rows, in one atomic transaction. It imports ALL sites of the chosen organisation, including line-only sites hidden by the display filter. Any conflict/error rolls back the whole catalogue import. It never imports other results in the search. Truncated search pages must be narrowed before import.

Existing organisations are resolved by exact approval number first, then exact company name. Ambiguous matches and inactive records block import. Existing company/site/approval fields, airport links and capability mappings are not overwritten. New site-specific capability rows preserve original rating/model wording; no ICAO aliases, countries, coordinates or airport links are guessed. New LBA records have source code LBA_DIRECTORY and a completed company-import batch recording the reviewer. Recent successful batches appear under Tools → Data imports.

Repeating an identical organisation snapshot returns the previous batch without duplicating records. If the published snapshot changes, this pilot stops and asks for an update/merge review rather than adding potentially contradictory capabilities or deleting disappeared sites. It is an additive first-import workflow, not a full synchronisation engine. Different source feeds can still describe the same capability separately; this version does not attempt fuzzy cross-source deduplication.

After importing ADAC, link its sites to the appropriate airports using the existing site-tracking controls and review EC135/BK117 type mappings. Only airport-linked sites produce maintenance badges beside a stop; unlocated sites remain in the catalogue. A facility's capability is not evidence the aircraft underwent maintenance there.

Next increment: source-update comparisons and reviewed merges, then background scheduling if needed. Establish site access/reuse conditions before scaling or scheduling. Missing source entries must never automatically delete records: LBA publication is consent-based.

Migration **phase20.sql** creates the saved-preview table and is registered with normal migration/startup. Deploy the Python code and compiled SPA using the normal backup/stop/pull/install/migrate/start process. No dependencies, Pi update or raw reprocessing are required. No ADAC data is seeded by deployment; import it through the page.
