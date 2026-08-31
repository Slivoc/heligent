# Phase 9: customer and base curation

Phase 9 provides a deliberately manual first link between imported companies,
approved addresses and Heligent's airport activity. It is available only in the
local admin application; public-demo mode continues to hide all administration
and mutation endpoints.

## Workflow

1. Open **Customers** in the primary navigation.
2. Search by company, site, postcode, approval number or existing airport.
3. Select a site and optionally mark its company as a customer.
4. Mark the individual site as an address of interest and add an internal note.
5. Search for an airport by name, IATA or ICAO code, select it and save.
6. Choose a processed UTC date to inspect airport activity, leading types,
   aircraft with movement evidence and the site's imported approval scope.

Customer status is stored on `company`, so it applies to every imported site
for that entity. Watch status, notes and the airport association are stored on
`company_site`. Manual links record their method and update time. Reimporting
regulatory data does not erase these curated fields.

## Attribution boundary

The activity panel reports ADS-B-derived candidates for the entire linked
airport. It does not establish that an aircraft entered the MRO's premises,
received maintenance or interacted with the customer. Multiple companies may
therefore show the same base activity when their sites are linked to the same
airport. Site-level visit attribution remains a later geofence/dwell feature.

## API

- `GET /api/company-sites?q=...&tracked=true|false`
- `GET /api/airports/search?q=...`
- `POST /api/company-sites/<site_id>/tracking`
- `GET /api/company-sites/<site_id>/activity?date=YYYY-MM-DD`

Mutations require the normal `X-Requested-With: HeligentAdmin` local-admin
header. Public-demo mode returns 404 for all four endpoint families.
