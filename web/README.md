# Heligent aviation-data conversation

This directory contains the responsive Phase 5 natural-language experience,
Phase 4 Explorer, and Phase 3 data-management interface.
It is built in two forms from the same React component:

- a Sites-compatible vinext build used for frontend validation;
- a static SPA bundled into the Python package and served by the local
  ingestion API.

The complete application is launched from the repository root with
`adsb-web`. The frontend-only development server is useful when changing the
interface:

```powershell
npm install
npm run dev
```

Validation commands:

```powershell
npm test
npm run lint
```

The operational application remains local by design. The server-side OpenAI
integration converts questions into a strict plan containing one query over
the read-only `nl_*` semantic schema. The server parses, bounds, cost-checks and
executes that query in a read-only PostgreSQL transaction. Data control
operates the downloader and sequential ingestion worker running on the same
machine.

The Flask-served `/demo` route is a deliberately minimal mobile surface for
showing the natural-language experience without exposing controls or settings.
