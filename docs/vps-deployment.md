# Heligent VPS deployment

## Recommended host

Use **Ubuntu Server 24.04 LTS (64-bit)**. It is a conservative fit for the
Python 3.12 and PostgreSQL packages used by this deployment and receives LTS
security maintenance. A practical starting size is 4 vCPU, 16 GB RAM and at
least 250 GB of NVMe storage. Prefer 8 vCPU/32 GB RAM for sustained backfills.
Size disk from measured PostgreSQL growth before committing to long retention;
the Raspberry Pi remains the long-term store for raw ADS-B files.

The preferred long-term shape runs Heligent on its own VPS. A supported
temporary mode can instead co-host it on Sproutt's VPS without merging the two
products. Co-hosting still uses separate Linux/PostgreSQL roles, databases,
virtual environments, configuration, services and release cycles.

## Network shape

Only SSH should listen publicly on the VPS itself:

- `127.0.0.1:5080`: maintenance UI and ingestion worker;
- ngrok outbound tunnel: public HTTPS UI with Microsoft/Google sign-in;
- `127.0.0.1:5100`: versioned intelligence API;
- separate VPS: Tailscale Serve privately proxies Sproutt to port 5100;
- Sproutt co-host: Sproutt calls port 5100 over loopback;
- PostgreSQL: local Unix socket only;
- Raspberry Pi feeder: private outbound HTTPS over Tailscale.

Colleagues do **not** install Tailscale to use the maintenance UI. They open the
ngrok URL and sign in. Tailscale remains useful for infrastructure connections
to the Pi and, after Heligent moves, between the two VPSs.

Do not open 5080, 5100 or 5432 in the VPS firewall. Do not use Tailscale Funnel
for the intelligence API.

## 1. Put the release on the VPS

Create a dedicated read-only GitHub deploy key **on the VPS**, while signed in
as the Linux account that will perform deployments. Do not create the private
key on a workstation or copy it into GitHub.

```bash
install -d -m 0700 ~/.ssh
ssh-keygen -t ed25519 -C "heligent-vps-deploy" \
  -f ~/.ssh/heligent_github -N ""
cat ~/.ssh/heligent_github.pub
```

In GitHub, open `Slivoc/heligent` -> **Settings** -> **Deploy keys** ->
**Add deploy key**. Name it `Heligent production VPS`, paste the displayed
public key, and leave **Allow write access** unchecked. Each VPS should have
its own deploy key.

Give this repository a distinct SSH host alias so the key cannot accidentally
be offered to unrelated GitHub repositories:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-heligent
  HostName github.com
  User git
  IdentityFile ~/.ssh/heligent_github
  IdentitiesOnly yes
EOF
chmod 0600 ~/.ssh/config
ssh -T git@github-heligent
```

On the first connection, compare the reported host-key fingerprint with
GitHub's published SSH fingerprints before accepting it. A successful deploy
key test may still say that GitHub does not provide shell access; that is
normal.

Then clone the tested release. The directory must contain `pyproject.toml`,
`schema/`, `src/` and `deploy/`.

```bash
sudo mkdir -p /srv/heligent
sudo chown "$USER":"$USER" /srv/heligent
git clone git@github-heligent:Slivoc/heligent.git /srv/heligent
cd /srv/heligent
```

The compiled SPA is included. Node.js is not required on the VPS.

## 2. Install the application and PostgreSQL

Review the installer, then run it from the repository. On a dedicated VPS use:

```bash
cd /srv/heligent
sudo bash deploy/vps/install-ubuntu.sh
```

On the existing Sproutt VPS use the explicit co-host mode:

```bash
cd /srv/heligent
sudo bash deploy/vps/install-ubuntu.sh --cohost-sproutt
```

Before the first co-host installation, confirm that the latest scheduled
Sproutt backup completed and record current free disk/RAM. Do not stop Sproutt:

```bash
systemctl status sproutt postgresql --no-pager
free -h
df -h /
sudo ss -lntp
```

It installs missing OS/Python dependencies, creates the unprivileged Linux and
PostgreSQL role `heligent`, creates `heligent_adsb`, installs the Python package
into `/srv/heligent/.venv`, applies every database migration, installs the three
systemd units, and copies configuration templates only when their destination
does not already exist. Existing PostgreSQL is reused. It deliberately does
not start anything while secrets are placeholders.

Co-host mode first requires the existing `/srv/sproutt` deployment and active
`sproutt` service. It adds systemd drop-ins that give the ingestion worker lower
CPU and disk-I/O priority, cap its memory at 3 GB, cap the API at 1 GB, reduce
the API thread count, and make Heligent preferable to Sproutt under out-of-
memory pressure. It does not edit nginx, UFW, Sproutt files or the `sproutt`
database.

Local PostgreSQL uses peer authentication. The service does not need a database
password and PostgreSQL is not exposed to the network.

## 3. Configure Heligent secrets

Generate independent secrets:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Edit `/etc/heligent/heligent.env` as root:

```bash
sudoedit /etc/heligent/heligent.env
```

- Put the first value in `HELIGENT_AUTH_PROXY_SECRET`.
- Put the second in `HELIGENT_API_TOKEN`.
- Set `HELIGENT_BOOTSTRAP_ADMIN_EMAILS` to your sign-in email exactly as the
  identity provider reports it.
- Set the Pi URL/token if it is ready. Both can be added later.
- Add an OpenAI API key only if the natural-language Explorer is required.

The bootstrap list inserts missing administrators; it does not reactivate or
overwrite an existing user on later restarts.

## 4. Configure ngrok and Microsoft sign-in

Install the ngrok apt package using the current commands from ngrok's Linux
download page:

```bash
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc \
  | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com bookworm main" \
  | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt update
sudo apt install ngrok
```

Save the account authtoken in the root-owned service config:

```bash
sudo ngrok config add-authtoken YOUR_NGROK_AUTHTOKEN \
  --config /etc/heligent/ngrok.yml
sudo chown root:heligent-ngrok /etc/heligent/ngrok.yml
sudo chmod 640 /etc/heligent/ngrok.yml
```

Reserve an HTTPS ngrok URL (or attach a custom domain) and put it in
`/etc/heligent/ngrok.env`.

For production Microsoft sign-in, register a custom application in Microsoft
Entra ID:

1. Choose a multi-tenant supported account type. ngrok's OAuth integration does
   not support Entra single-tenant applications.
2. Add the Web redirect URI `https://idp.ngrok.com/oauth2/callback`.
3. Grant the required user-reading permission and create a client secret.
4. Put the application client ID and secret value in
   `/etc/heligent/ngrok-traffic-policy.yml`.
5. Replace the proxy-secret placeholder in that file with the exact first
   secret generated above.

Keep the ngrok config, policy and environment files mode `0640`, owned by
`root:heligent-ngrok`. Keep `heligent.env` mode `0640`, owned by
`root:heligent`. The separate service users prevent the gateway from reading
the database, Pi, OpenAI and Sproutt API credentials.
The policy strips spoofed identity headers, runs OAuth, then adds the verified
email and the shared proxy secret. Heligent independently checks that email
against its active-user table.

Google OAuth can be used instead by changing the provider to `google`, using a
Google OAuth client, and using Google's user-info email/profile scopes. The
Heligent side is unchanged because it consumes only a verified email header.

## 5. Start the maintenance UI

```bash
sudo systemctl enable --now heligent-web.service
sudo systemctl enable --now heligent-ngrok.service
sudo systemctl status heligent-web.service heligent-ngrok.service
```

Open the reserved ngrok URL. After identity-provider sign-in, the bootstrap
administrator should see the application. `/ngrok/logout` clears the ngrok
session cookie.

Manage colleagues from the server until a user-management screen is added:

```bash
sudo -u heligent env DATABASE_URL=postgresql:///heligent_adsb \
  /srv/heligent/.venv/bin/heligent-users add colleague@example.com --role ANALYST

sudo -u heligent env DATABASE_URL=postgresql:///heligent_adsb \
  /srv/heligent/.venv/bin/heligent-users list

sudo -u heligent env DATABASE_URL=postgresql:///heligent_adsb \
  /srv/heligent/.venv/bin/heligent-users deactivate colleague@example.com
```

Roles are:

- `VIEWER`: read dashboards and run Explorer queries;
- `ANALYST`: viewer access plus queue/reprocess ingestion and edit curated
  operator/customer mappings;
- `ADMIN`: analyst access plus manage users.

Mutating requests are written to `heligent_audit_event` with identity, role,
request ID, route, response status and client address. The final active admin
cannot be removed or demoted.

## 6. Configure the private Sproutt API

### Sproutt co-host

Start the loopback-only API:

```bash
sudo systemctl enable --now heligent-intelligence-api.service
```

Put these values in Sproutt's server environment:

```text
AVIATION_INTELLIGENCE_API_URL=http://127.0.0.1:5100
AVIATION_INTELLIGENCE_API_TOKEN=<same value as HELIGENT_API_TOKEN>
AVIATION_INTELLIGENCE_API_TIMEOUT_SECONDS=30
```

This still uses the versioned HTTP API and bearer token. Sproutt must not read
`heligent_adsb` directly merely because both databases currently share a
PostgreSQL server. Localhost HTTP can later be replaced with the Tailscale URL
without changing product logic when Heligent moves.

### Separate VPS

Install and join Tailscale on both VPSs:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Tag the machines separately in the Tailscale admin console (or with tagged auth
keys), for example `tag:heligent` and `tag:sproutt`. A least-privilege grant is:

```json
{
  "tagOwners": {
    "tag:heligent": ["autogroup:admin"],
    "tag:sproutt": ["autogroup:admin"]
  },
  "grants": [
    {
      "src": ["tag:sproutt"],
      "dst": ["tag:heligent"],
      "ip": ["tcp:443"]
    }
  ]
}
```

Merge this with the existing tailnet policy rather than replacing unrelated
rules. Then start the API and publish only that loopback port with Serve:

```bash
sudo systemctl enable --now heligent-intelligence-api.service
sudo tailscale serve --bg 5100
tailscale serve status
```

Put the resulting tailnet HTTPS URL in Sproutt's
`AVIATION_INTELLIGENCE_API_URL` and put Heligent's `HELIGENT_API_TOKEN` in
Sproutt's `AVIATION_INTELLIGENCE_API_TOKEN`. The bearer token remains required
even inside the tailnet.

## 7. Firewall and validation

For a new UFW configuration, keep the current SSH session open while applying:

```bash
sudo ufw allow OpenSSH
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable
sudo ss -lntp
```

Expected TCP listeners are SSH plus loopback 5080 and 5100. PostgreSQL normally
uses a Unix socket; if it has a TCP listener, ensure it is loopback-only.

Validate locally and through both gateways:

```bash
curl http://127.0.0.1:5080/health
curl http://127.0.0.1:5100/health
journalctl -u heligent-web -u heligent-ngrok \
  -u heligent-intelligence-api --since today
```

From the Sproutt VPS (or directly over localhost in co-host mode):

```bash
curl -H "Authorization: Bearer $AVIATION_INTELLIGENCE_API_TOKEN" \
  "$AVIATION_INTELLIGENCE_API_URL/api/v1/coverage"
```

Also queue and process one completed UTC day through the maintenance UI before
starting a large backfill. Confirm coverage, a known-tail daily response and a
Europe hub ranking.

## Operations

### Deploy an update

Stop the worker before replacing code, install the tested release, migrate, and
restart all processes:

```bash
sudo systemctl stop heligent-ngrok heligent-intelligence-api heligent-web
cd /srv/heligent
git pull --ff-only
sudo /srv/heligent/.venv/bin/python -m pip install --editable /srv/heligent
sudo -u heligent env DATABASE_URL=postgresql:///heligent_adsb \
  /srv/heligent/.venv/bin/heligent-migrate
sudo systemctl start heligent-web heligent-intelligence-api heligent-ngrok
```

`heligent-migrate` is idempotent and uses a PostgreSQL advisory lock, so the
systemd pre-start checks are safe if two services start together.

### Backups

Enable encrypted VPS/provider snapshots and take a PostgreSQL custom-format
backup before every deployment. Store a copy outside this VPS:

```bash
sudo install -d -o heligent -g heligent -m 0750 /var/backups/heligent
sudo -u heligent sh -c \
  'pg_dump --format=custom heligent_adsb > /var/backups/heligent/heligent_adsb-$(date -u +%Y%m%dT%H%M%SZ).dump'
```

Regularly restore a backup into a separate disposable database to prove it is
usable. PostgreSQL backups do not replace the raw archive on the Pi; back up
the Pi's SQLite queue/manifests separately and monitor its SSD health.

### Logs and health

```bash
systemctl --failed
journalctl -u heligent-web -f
journalctl -u heligent-intelligence-api -f
journalctl -u heligent-ngrok -f
sudo -u postgres psql -d heligent_adsb -c \
  "select status, count(*) from dataset_day group by status order by status;"
```

Never paste environment files, ngrok policy contents or bearer tokens into
support logs. Rotate the relevant secret in both peers if one is disclosed.
