# Raspberry Pi raw ADS-B archive

The `adsb-archive` command keeps verified ADSB.lol preferred releases on an
SSD without requiring PostgreSQL. It uses a SQLite queue on the same SSD,
resumes `.part` downloads, gives the daily latest release priority over
backfills, writes a provenance manifest, and never deletes a completed release.

ADSB.lol releases are split tar archives. Keep all parts and `manifest.json`
together; do not concatenate or unpack them just for storage.

## Storage sizing

The measured 2026-08-20 release is 4,075,250,176 bytes (3.80 GiB). Recent
releases vary, so budget about 4 GiB/day:

- 30 days: about 120 GiB;
- 180 days: about 720 GiB;
- 365 days: about 1.35 TiB at the measured rate, before filesystem headroom.

The worker keeps 20 GiB free by default. If a download would cross that reserve,
it fails safely, retains any resumable `.part` file, and stops before consuming
the rest of the queue. Free space and restart the worker service. It does not
implement an automatic retention/deletion policy.

## Recommended operating system and connections

Use **Raspberry Pi OS Lite (64-bit)**, the current non-legacy release. Lite has
no desktop environment, which is ideal for a headless archive appliance. The
Pi 400 is supported by the 64-bit image, and the project requires Python 3.11
or newer.

Use Raspberry Pi Imager to write the OS to a microSD card. In Imager's OS
customisation screen:

- set a hostname such as `adsb-archive`;
- create your own username;
- enable SSH with public-key authentication;
- configure Wi-Fi only if you will not use wired Ethernet.

The operating system can stay on the microSD card, but the archive data and its
SQLite queue should live on the SSD. Format the SSD as ext4, mount it at
`/mnt/adsb-archive`, and connect it to one of the Pi 400's blue USB 3 ports.
Add the SSD to `/etc/fstab` by filesystem UUID (use `sudo blkid` to find it),
with `nofail,x-systemd.device-timeout=30s` options, then confirm the setup with
`sudo mount -a` and `mountpoint /mnt/adsb-archive` before enabling the services.

USB 3 and SSH do different jobs:

- **USB 3 connects the SSD physically to the Pi.** It provides the storage path
  and sufficient local disk throughput.
- **SSH connects your computer to the Pi over the network.** Use it to install,
  configure, monitor and queue downloads without attaching a screen or keyboard.

You can SSH over the local network immediately after installation, then install
Tailscale and SSH over the tailnet when away from home. Wired Ethernet is
preferable for the Pi's multi-gigabyte downloads, but SSH works over Ethernet,
Wi-Fi or Tailscale.

## Commands

Install the repository into a virtual environment, then select the SSD root:

```bash
export ADSB_ARCHIVE_ROOT=/mnt/adsb-archive

# Queue specific older days or an inclusive range.
adsb-archive queue 2026-08-20 2026-08-21
adsb-archive queue-range 2026-08-01 2026-08-07

# Discover the actual newest preferred completed day and priority-queue it.
adsb-archive queue-latest

# Process the queue now, or run the persistent worker.
adsb-archive run
adsb-archive worker

# Inspect/retry work and periodically re-hash retained assets.
adsb-archive status
adsb-archive status --json
adsb-archive retry 2026-08-20
adsb-archive retry                 # all failed dates
adsb-archive verify 2026-08-20
adsb-archive verify                # every completed date; potentially slow
adsb-archive verify-next           # one never/least-recently checked day
```

The latest job has priority 100 and manual backfills have priority 0. A running
asset download is allowed to finish; the latest release is selected before the
next backfill begins. Failed daily-latest items are requeued by the next daily
check so a transient error does not silently create a gap. Only one worker can
download at a time. If power is lost, the next worker requeues the interrupted
item and resumes its `.part` file.

The layout is:

```text
/mnt/adsb-archive/
├── archive-queue.sqlite3
├── .archive-worker.lock
└── releases/
    └── v2026.08.20-planes-readsb-staging-0/
        ├── manifest.json
        ├── v2026.08.20-planes-readsb-staging-0.tar.aa
        ├── v2026.08.20-planes-readsb-staging-0.tar.ab
        └── v2026.08.20-planes-readsb-staging-0.tar.ac
```

## Pi installation and daily timer

These service files assume the checkout is `/opt/heligent` and its virtual
environment is `/opt/heligent/.venv`. Replace `YOUR_USER` with the Pi account
that owns the SSD directory.

```bash
sudo apt update
sudo apt install -y git python3-venv sqlite3
sudo mkdir -p /opt/heligent /mnt/adsb-archive /etc/heligent
sudo chown -R YOUR_USER: /opt/heligent /mnt/adsb-archive

# Put this repository in /opt/heligent, then:
cd /opt/heligent
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

sudo cp deploy/raspberry-pi/adsb-archive.env.example \
  /etc/heligent/adsb-archive
sudo chmod 600 /etc/heligent/adsb-archive
sudo cp deploy/raspberry-pi/adsb-archive-*.service /etc/systemd/system/
sudo cp deploy/raspberry-pi/adsb-archive-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adsb-archive-worker@YOUR_USER.service
sudo systemctl enable --now adsb-archive-latest@YOUR_USER.timer
sudo systemctl enable --now adsb-archive-verify@YOUR_USER.timer
sudo systemctl enable --now adsb-archive-api@YOUR_USER.service
```

The timer checks at 06:15 UTC with up to 15 minutes of random delay and catches
up after downtime (`Persistent=true`). It discovers the newest date actually
published in `PREFERRED_RELEASES.txt`; it does not assume yesterday is ready.
The continuously running worker notices the new queue item within 30 seconds.
The integrity timer re-hashes one never or least-recently verified completed day
each night, so coverage rotates through the archive without an increasingly long
full-disk scan. A corrupt day fails visibly in the service journal and remains
the next verification candidate until it is repaired.

All archive services refuse to start unless `/mnt/adsb-archive` is an actual
mount point. This prevents a disconnected or failed SSD from silently redirecting
multi-gigabyte downloads onto the Pi's microSD card. If you deliberately choose
a different mount point, update `ConditionPathIsMountPoint` in each installed
archive service as well as `ADSB_ARCHIVE_ROOT`.

Useful checks:

```bash
systemctl list-timers 'adsb-archive-*'
systemctl status adsb-archive-worker@YOUR_USER.service
journalctl -u adsb-archive-worker@YOUR_USER.service -f
systemctl status adsb-archive-verify@YOUR_USER.timer
mountpoint /mnt/adsb-archive
ADSB_ARCHIVE_ROOT=/mnt/adsb-archive /opt/heligent/.venv/bin/adsb-archive status
```

A read-only fine-grained `GITHUB_TOKEN` is optional. Put it in
`/etc/heligent/adsb-archive`, not the repository, if backfilling enough dates to
encounter GitHub's anonymous API limit.

## Updating the Pi checkout

Updating the application does not rewrite completed archives or the SQLite
queue. From an SSH session on the Pi, pull the code, refresh the virtual
environment, install the current unit files, and restart the services:

```bash
cd /opt/heligent
git pull --ff-only
.venv/bin/pip install -e .
sudo cp deploy/raspberry-pi/adsb-archive-*.service /etc/systemd/system/
sudo cp deploy/raspberry-pi/adsb-archive-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adsb-archive-worker@YOUR_USER.service
sudo systemctl enable --now adsb-archive-latest@YOUR_USER.timer
sudo systemctl enable --now adsb-archive-verify@YOUR_USER.timer
sudo systemctl enable --now adsb-archive-api@YOUR_USER.service
sudo systemctl restart adsb-archive-worker@YOUR_USER.service
sudo systemctl restart adsb-archive-api@YOUR_USER.service
```

Use a read-only GitHub deploy key on the Pi if the repository is private. The
Pi needs its own private key; do not copy the VPS private key between machines.
After updating, check the four units and call the API's `/health` endpoint over
its Tailscale Serve URL.

## Feeding an archived day to the main app

The Pi runs a read-only API on loopback port 5090. It exposes only completed
releases recorded in the archive queue, checks manifest paths and file sizes,
supports HTTP Range requests for resumable transfers, and requires a bearer
token. It cannot add queue items, delete files or browse arbitrary paths.

Generate one token and place the same value in the Pi service environment and
the main app's private environment:

```bash
openssl rand -hex 32
sudoedit /etc/heligent/adsb-archive
```

Set `ADSB_ARCHIVE_API_TOKEN` to that value on the Pi, restart the API, then make
the loopback service privately available through Tailscale Serve:

```bash
sudo systemctl restart adsb-archive-api@YOUR_USER.service
sudo tailscale serve --bg 5090
tailscale serve status
```

On the main app machine, configure the HTTPS URL printed by Tailscale and the
same token:

```bash
ADSB_ARCHIVE_API_URL=https://adsb-archive.example-tailnet.ts.net
ADSB_ARCHIVE_API_TOKEN=the-same-random-token
```

Restart `adsb-web`. The Data control screen then enables two choices:

- **Direct from ADSB.lol** — the existing GitHub release path;
- **Raspberry Pi archive API** — discovers and downloads the Pi's verified copy.

The selected source is stored on every queue item and remains stable across app
or worker restarts. There is deliberately no automatic fallback: if a selected
day is absent from the Pi, the job fails clearly and can be retried with the
direct source. In both modes, the main machine downloads into its normal
transient raw directory, verifies SHA-256, parses locally, and applies the
existing raw-retention setting.

After a parser upgrade, Data control can rebuild a date range from the Pi: set
the source to **Raspberry Pi archive API**, choose up to 31 days, and enable
**Reprocess completed dates in this range**. The VPS processes the dates
sequentially and deletes only its transferred copy after each successful
commit. The retained Pi release is never changed. The flight/visit rebuild and
Maintenance Pulse validation procedure is in
[`phase15-flight-visits-and-maintenance-foundation.md`](phase15-flight-visits-and-maintenance-foundation.md).

The command-line equivalent is:

```bash
adsb-ingest ingest-day \
  --date 2026-08-20 \
  --raw-source pi \
  --reprocess
```

The URL and token are read from `ADSB_ARCHIVE_API_URL` and
`ADSB_ARCHIVE_API_TOKEN`. Direct mode remains the default.

## Copying an archived day manually

Copy the complete release directory from the Pi to the machine that runs the
application and PostgreSQL. For example, from that machine:

```bash
rsync -av --partial \
  YOUR_USER@adsb-archive:/mnt/adsb-archive/releases/v2026.08.20-planes-readsb-staging-0/ \
  /data/raw/v2026.08.20-planes-readsb-staging-0/

adsb-ingest ingest-day \
  --date 2026-08-20 \
  --raw-dir /data/raw \
  --keep-raw \
  --reprocess
```

The Pi's source copy is unaffected because parsing uses the copied directory.
`--keep-raw` also retains the receiving copy; omit it only if that second copy
should be deleted after a successful database commit.

## Remote administration: SSH over Tailscale

The archive worker itself needs no inbound port. Install Tailscale on the Pi and
use SSH/SFTP over the tailnet to inspect the queue, add backfills, read logs, or
copy a release. Tailscale Serve publishes only the read-only loopback feeder API
inside the tailnet; do not use Funnel or ngrok for it.

After installing Tailscale on the Pi and your computer, a normal SSH session is
enough:

```bash
ssh YOUR_USER@adsb-archive
```

MagicDNS normally makes the hostname available inside the tailnet. Otherwise,
use the Pi's Tailscale IP shown by `tailscale ip -4`. Keep password SSH disabled
once key-based access is confirmed, and do not forward port 22 on the router.
