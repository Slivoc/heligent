# Capability-to-aircraft review

Open **Capability mappings** in the application navigation, or **Review type mappings** on an aircraft capability in the Stops map.

Search for a company, approval number or uploaded wording (for example `BK117`). Select the exact capability record, enter an ICAO code such as `EC45`, and record an evidence URL and review notes. Original imported wording is preserved.

- **Possible family** is an amber lead, not verified suitability. Use this for broad series wording until its precise scope has been checked.
- **Reviewed type** with restricted variants remains amber: the map currently knows the ICAO code, not the aircraft's certified variant.
- A type-wide reviewed mapping requires explicit confirmation. It produces a site match only for a site-specific capability; company-wide records remain company-wide.
- Approval status and validity dates are checked live. A match does not establish the required check, tooling, availability, historical approval or that maintenance occurred.

## Map a phrase once

The default **All entries with this exact wording** option saves a shared rule. For example, map the displayed EC135 model phrase to EC35 once and all current and future entries with that same phrase can use it. Only case and whitespace are ignored: different names, punctuation, variants and broader family wording need their own review. Nothing is seeded or guessed automatically.

The aircraft model field is used when available (including LBA imports); otherwise the full original limitation text is used for older imports. The exact phrase is shown before saving. Confirm the shared scope explicitly. Shared rules have their own edit/withdraw controls and revision history, available from any capability with that phrase.

Each facility retains its own base/line flags, limitations, approval validity and site/company scope. Sharing an aircraft-name interpretation never copies another site's approval. Broad BK117 wording should remain **Possible family**, and restricted variants remain amber. A shared rule is not a finding that a particular check can be performed.

Choose **This capability only (override)** for an exception. A site-specific record takes precedence for the same ICAO code, even when stale or withdrawn; a shared rule cannot silently reactivate it. An exact imported ICAO code remains independent source evidence. Existing site-specific reviews are not automatically converted into shared rules.

For G-LZZI, find the relevant imported MBB-BK117 capability and add EC45 as a **possible family** mapping with supporting evidence. Do not infer blanket approval from the family name alone.

Analysts and administrators may save mappings; viewers can inspect them. Edits retain previous revisions, reviewer and time. Uncheck Active to withdraw a mapping rather than delete its history. Concurrent edits are rejected and require a reload. Changes to the imported capability's model, limitation, rating, site or maintenance scope make its site-specific mapping stale until re-reviewed. Shared rules stop applying when the selected wording changes; unchanged aircraft wording can still be interpreted under the site's current limitations. Importers must retain stable capability IDs for site-specific reviews.

Reload the Stops map after saving. Its type-match filter includes possible family matches, but not stale mappings. Missing matches are missing evidence, not evidence of incapability.

## Deployment

Schema phase21 adds `capability_phrase_mapping` and `capability_phrase_revision` alongside the phase18 site-specific tables. It is registered with `heligent-migrate` and application startup. Include the generated `web_static` assets in your commit, pull on the VPS, and follow the existing migration/restart procedure. Take the usual database backup first. No Pi change, LBA reimport, raw-file download or aircraft-data reprocessing is required. No mappings are automatically seeded.

## LBA / ADAC ingestion assessment

The [LBA technical organisations directory](https://iauskunft.lba.de/tb/) can be queried by organisation name. A read-only ADAC search on 7 September 2026 returned ADAC Heliservice GmbH, approval DE.145.0058, with individual operating locations and ratings/models. The Sankt Augustin site includes A3 base and line maintenance for EC135 and MBB-BK117; other locations may be line-only. These broad model names still need reviewed ICAO/variant mappings.

The directory is a JSF form with session/view-state and AJAX responses, rather than an advertised API in the inspected interface. Tools now supports **fetch → select organisation → confirm import**, preserving site scope. Changed repeat imports still need a future update/merge workflow. See [Tools / Data imports](tools-data-imports.md).

Recommended adapter:

1. Fetch a small, targeted organisation search, respecting the site's access/reuse conditions and using conservative request rates.
2. Preserve the fetched response and timestamp for audit; parse organisation, approval, site address, rating, model and base/line scope separately.
3. Preview changes before importing through the existing company/capability import workflow. Match ADAC Heliservice separately from ADAC Luftrettung; do not merge on the word ADAC.
4. Use stable source IDs and retain original wording. Never turn company approval into blanket site capability. Geocoding addresses and verifying airport links is a separate enrichment step.
5. Do not remove approvals because they disappear from search: LBA publication is consent-based and the directory does not claim completeness.

Start with ADAC as a pilot before expanding to the wider directory.
