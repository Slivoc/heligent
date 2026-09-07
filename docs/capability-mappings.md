# Capability-to-aircraft review

Open **Capability mappings** in the application navigation, or **Review type mappings** on an aircraft capability in the Stops map.

Search for a company, approval number or uploaded wording (for example `BK117`). Select the exact capability record, enter an ICAO code such as `EC45`, and record an evidence URL and review notes. Original imported wording is preserved.

- **Possible family** is an amber lead, not verified suitability. Use this for broad series wording until its precise scope has been checked.
- **Reviewed type** with restricted variants remains amber: the map currently knows the ICAO code, not the aircraft's certified variant.
- A type-wide reviewed mapping requires explicit confirmation. It produces a site match only for a site-specific capability; company-wide records remain company-wide.
- Approval status and validity dates are checked live. A match does not establish the required check, tooling, availability, historical approval or that maintenance occurred.

For G-LZZI, find the relevant imported MBB-BK117 capability and add EC45 as a **possible family** mapping with supporting evidence. Do not infer blanket approval from the family name alone. This does not create a global BK117 alias or automatically map unrelated sites.

Analysts and administrators may save mappings; viewers can inspect them. Edits retain previous revisions, reviewer and time. Uncheck Active to withdraw a mapping rather than delete its history. Concurrent edits are rejected and require a reload. Changes to the imported capability's model, limitation, rating, site or maintenance scope make its mapping stale until re-reviewed. Importers must retain stable capability IDs; replacement records require new reviews.

Reload the Stops map after saving. Its type-match filter includes possible family matches, but not stale mappings. Missing matches are missing evidence, not evidence of incapability.

## Deployment

Schema phase18 adds `capability_aircraft_mapping` and `capability_mapping_revision`; it is registered with `heligent-migrate` and application startup. Include the generated `web_static` assets in your commit, pull on the VPS, and follow the existing migration/restart procedure. Take the usual database backup first. No Pi change, raw-file download or aircraft-data reprocessing is required. No mappings are automatically seeded.

## LBA / ADAC ingestion assessment

The [LBA technical organisations directory](https://iauskunft.lba.de/tb/) can be queried by organisation name. A read-only ADAC search on 7 September 2026 returned ADAC Heliservice GmbH, approval DE.145.0058, with individual operating locations and ratings/models. The Sankt Augustin site includes A3 base and line maintenance for EC135 and MBB-BK117; other locations may be line-only. These broad model names still need reviewed ICAO/variant mappings.

The directory is a JSF form with session/view-state and AJAX responses, rather than an advertised API in the inspected interface. A subsequent first-pass adapter now supports **fetch and preview only** under Tools; catalogue import is not implemented. See [Tools / Data imports](tools-data-imports.md).

Recommended adapter:

1. Fetch a small, targeted organisation search, respecting the site's access/reuse conditions and using conservative request rates.
2. Preserve the fetched response and timestamp for audit; parse organisation, approval, site address, rating, model and base/line scope separately.
3. Preview changes before importing through the existing company/capability import workflow. Match ADAC Heliservice separately from ADAC Luftrettung; do not merge on the word ADAC.
4. Use stable source IDs and retain original wording. Never turn company approval into blanket site capability. Geocoding addresses and verifying airport links is a separate enrichment step.
5. Do not remove approvals because they disappear from search: LBA publication is consent-based and the directory does not claim completeness.

Start with ADAC as a pilot before expanding to the wider directory.
