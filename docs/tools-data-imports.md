# Tools / Data imports — first pass

## Hexes without tail numbers

Tools includes an unidentified-aircraft page. It ranks position-bearing daily records with NULL/blank registration by estimated airborne hours. The default is the latest processed week, with a maximum 31-day range and 50 hexes per page. It shows observed dates, positions, recorded types, up to 20 distinct callsigns, and the current aircraft registration separately. Totals include only the missing-registration days, not the aircraft's entire history. Records whose current registration is populated remain visible if historical daily metadata is missing. Callsigns are evidence for review, not automatic identity assignments. Failed/unprocessed dates are excluded and coverage is displayed.

### Assign a tail number

Analysts/admins can select **Assign tail number**, enter the registration, optional ICAO type, verified effective start date, optional inclusive end date, evidence URL and notes. Preview shows all affected daily records in that effective interval, not just those on the current list page. Confirm saves a dated override and updates historical metadata through the existing identity trigger. Blank end date covers future ingestion. Flight/visit records and raw archives are not reprocessed.

Overlapping overrides, a different registration/type already in the interval, or the registration appearing under another hex in the interval block assignment. Changed underlying records invalidate the preview. This first version creates new non-overlapping corrections only; editing/reversing existing corrections requires administrator review. Do not bypass conflicts by choosing arbitrary dates.

Migration **phase19.sql** adds an audit table retaining the assignment, reviewer and previous historical/current identity values. It is registered with normal migration/startup. Deploy the compiled frontend too. No identity is pre-seeded. The Stops map reads corrected daily records immediately; existing analytics caches and Sproutt snapshots still need their normal refresh/sync. No whole-database cache rebuild runs inside the assignment request.

Open **Tools → Data imports → Open LBA preview** (direct link `/#tools/lba`). Capability mappings now sits under Tools too; its old direct link and Stops map review buttons still work.

Analysts/admins can fetch an organisation search such as ADAC. The server makes two bounded HTTPS requests to the fixed LBA directory, preserving organisation → approval → site → rating → model wording. Requests are limited to one search per 30 seconds per app process; response size is capped at 4 MiB. Unexpected layouts stop the preview. Pagination is displayed and incomplete results are flagged; this version never crawls the full directory.

The preview is transient, not a database import or a comparison with existing records. Download JSON to retain its parsed contents, timestamp, source URL and response hash. Raw responses and run history are not persisted yet. Base-maintenance filtering uses literal source wording and does not validate suitability for a particular aircraft. No coordinates, ICAO aliases or cross-company merges are inferred.

Next increment: durable background staging jobs with source snapshots, stable identifiers, existing-record comparison, curated-field conflict protection and explicit approved imports. Establish site access/reuse conditions before scaling or scheduling. Missing source entries must never automatically delete records: LBA publication is consent-based.

This first pass adds no database migration or dependencies. Deploy the Python code and compiled SPA using the normal stop/pull/install/start process. It does not modify the Pi or require reprocessing. Phase18 from the previous capability-mapping change remains required for that feature.
