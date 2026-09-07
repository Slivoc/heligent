# Tools / Data imports — first pass

Open **Tools → Data imports → Open LBA preview** (direct link `/#tools/lba`). Capability mappings now sits under Tools too; its old direct link and Stops map review buttons still work.

Analysts/admins can fetch an organisation search such as ADAC. The server makes two bounded HTTPS requests to the fixed LBA directory, preserving organisation → approval → site → rating → model wording. Requests are limited to one search per 30 seconds per app process; response size is capped at 4 MiB. Unexpected layouts stop the preview. Pagination is displayed and incomplete results are flagged; this version never crawls the full directory.

The preview is transient, not a database import or a comparison with existing records. Download JSON to retain its parsed contents, timestamp, source URL and response hash. Raw responses and run history are not persisted yet. Base-maintenance filtering uses literal source wording and does not validate suitability for a particular aircraft. No coordinates, ICAO aliases or cross-company merges are inferred.

Next increment: durable background staging jobs with source snapshots, stable identifiers, existing-record comparison, curated-field conflict protection and explicit approved imports. Establish site access/reuse conditions before scaling or scheduling. Missing source entries must never automatically delete records: LBA publication is consent-based.

This first pass adds no database migration or dependencies. Deploy the Python code and compiled SPA using the normal stop/pull/install/start process. It does not modify the Pi or require reprocessing. Phase18 from the previous capability-mapping change remains required for that feature.
