# Phase 7 public demo boundary

Phase 7 prepares the minimal `/demo` experience for a small public audience.
It does not add user accounts. For a friends-and-colleagues demonstration,
accounts would introduce password storage, recovery, verification, sessions and
administration without improving the core demonstration.

## Public-mode contract

Start the internet-facing process with `--public-demo --no-worker`. In this
mode the application exposes only:

- `/`, which redirects to `/demo`;
- `/demo` and compiled static assets;
- `/api/query/config`;
- `/api/query`;
- `/health`.

Analytics Explorer, dataset status, queue control, retry, cancellation and
reprocessing endpoints return 404 even if a caller supplies the old local UI
header. Query debug dumps are disabled. The normal local application is
unchanged when public mode is off.

The public query endpoint defaults to these in-process limits:

- 10 accepted questions per client address in a sliding 60-second window;
- 250 accepted questions globally in a sliding 24-hour window;
- 2 model queries in flight at once.

Configure them with `HELIGENT_QUERY_PER_MINUTE`,
`HELIGENT_QUERY_PER_DAY`, and `HELIGENT_QUERY_MAX_CONCURRENT`. A rejected
request returns HTTP 429 with `Retry-After` and rate-limit headers. Limits reset
when the process restarts and are intentionally designed for one small demo
instance; a larger or multi-process service should add a shared limiter at the
reverse proxy or in a network store.

## Recommended VPS process split

Run two processes against the restored PostgreSQL database:

```text
Internet -> HTTPS reverse proxy -> 127.0.0.1:5080 public demo, no worker
SSH tunnel only              -> 127.0.0.1:5081 admin UI plus worker
```

Example public command:

```bash
python -m adsb_ingest.webapp \
  --host 127.0.0.1 --port 5080 \
  --public-demo --no-worker --trusted-proxy-count 1
```

Example private admin/worker command:

```bash
python -m adsb_ingest.webapp --host 127.0.0.1 --port 5081
```

Reach the private interface from an operator machine with an SSH tunnel:

```bash
ssh -L 5081:127.0.0.1:5081 user@your-vps
```

Then open `http://127.0.0.1:5081` locally. Do not proxy port 5081 publicly.

## Reverse proxy requirements

- Terminate HTTPS and redirect plain HTTP to HTTPS.
- Proxy only to the loopback-bound public process.
- Forward one trusted layer of `X-Forwarded-For`, `X-Forwarded-Proto`, and
  `Host`; keep `--trusted-proxy-count 1` only when exactly one trusted proxy is
  in front of the app.
- Apply a small request-body limit and optionally duplicate the query rate
  limit at the proxy.
- Keep PostgreSQL, the admin port and the Waitress port closed in the VPS
  firewall; expose only SSH, HTTP and HTTPS.

Public responses include a restrictive content security policy, frame denial,
feature restrictions and HSTS when the trusted proxy reports HTTPS.

## Secrets and data migration

Store `DATABASE_URL` and `OPENAI_API_KEY` in a root-readable service environment
file, not in the repository. `.env.example` documents the variables but is not
loaded automatically.

Migrate the current database with a PostgreSQL custom-format dump and restore
it before starting either service. The ingestion ledger is included, so queueing
a range on the VPS skips dates already marked `PROCESSED`. The raw archives do
not need transfer because successfully committed raw files were deleted.

## When accounts become worthwhile

Add individual accounts only when Heligent needs persistent user history,
per-user quotas, private datasets, roles, revocation or an audit trail. A later
small closed beta could instead use an identity-aware reverse proxy without
putting password handling into Heligent itself.
