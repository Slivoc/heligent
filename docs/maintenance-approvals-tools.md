# Maintenance approvals in Tools

Tools opens on **Maintenance approvals** (`#tools`). Its separate pages are **Aircraft identity** (`#tools/identity`), **Activity ingestion** (`#tools/activity`) and **Reference data** (`#tools/reference`). Legacy source, LBA and unidentified-aircraft links still work. Identity gap reports run only on the Aircraft identity page.

The maintenance page reads existing imported approval evidence. Filter by source, approving authority, capability kind and geographic evidence. **Facility country** uses only the recorded site's country; **Company country** shows domicile and does not locate approved facilities. Unlocated scope remains visible in the totals. Country presence does not establish complete national coverage.

Choose a country to filter the MRO directory, then select an organisation. Scope is grouped into aircraft, engines, components, specialist services, services and other entries. Each row retains original wording, base/line privileges, authority and approval number, site/company scope, dates and the original source. Aircraft interpretations remain distinct from regulatory wording; restricted variants and stale mappings need review. Scope pagination rejects a changed dataset revision rather than combining different versions.

The source section keeps snapshot, import, fetch and recorded verification dates separate. Unknown source age is not replaced with a recent import time. Source workspaces list imported facility and company countries separately, including company-only EASA evidence. Missing LBA site countries remain unknown; the source authority is not used to infer a physical country.

The shared read-only API contract is documented in [intelligence-api.md](intelligence-api.md). These pages do not import, geocode or mutate approval records. The existing LBA preview/import and type-review workflows remain explicit analyst actions.

Build the served SPA from `web` using `npm run build:spa`. Browser checks use intercepted fixture responses and issue no modifying API requests:

```powershell
node tests/tools-maintenance.browser.cjs
node tests/maintenance-approvals.browser.cjs
```

Set `TOOLS_MAINTENANCE_URL` to a local preview URL (default `http://127.0.0.1:5089`) and `PLAYWRIGHT_CHANNEL=chrome` when using the installed Chrome browser instead of Playwright's bundled Chromium.
