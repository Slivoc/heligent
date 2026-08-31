#!/usr/bin/env bash
set -euo pipefail

COHOST_SPROUTT=false
case "${1:-}" in
  "") ;;
  --cohost-sproutt) COHOST_SPROUTT=true ;;
  *)
    echo "Usage: sudo bash deploy/vps/install-ubuntu.sh [--cohost-sproutt]" >&2
    exit 2
    ;;
esac

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "${APP_ROOT}" != "/srv/heligent" ]]; then
  echo "Place the repository at /srv/heligent before running this installer." >&2
  echo "Current repository: ${APP_ROOT}" >&2
  exit 1
fi
cd "${APP_ROOT}"

export DEBIAN_FRONTEND=noninteractive
apt-get update
packages=(
  ca-certificates curl git jq openssl ufw
  python3 python3-venv python3-dev build-essential
)
if ! command -v psql >/dev/null 2>&1; then
  packages+=(postgresql postgresql-contrib)
fi
apt-get install -y "${packages[@]}"

if ! systemctl is-active --quiet postgresql; then
  echo "PostgreSQL must be running before Heligent can be installed." >&2
  exit 1
fi
if [[ "${COHOST_SPROUTT}" == true ]]; then
  if ! systemctl is-active --quiet sproutt; then
    echo "--cohost-sproutt requires the existing sproutt service to be active." >&2
    exit 1
  fi
  if [[ ! -d /srv/sproutt ]]; then
    echo "--cohost-sproutt requires the existing /srv/sproutt deployment." >&2
    exit 1
  fi
fi

if ! id heligent >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /srv/heligent --no-create-home \
    --shell /usr/sbin/nologin heligent
fi
if ! id heligent-ngrok >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /nonexistent --no-create-home \
    --shell /usr/sbin/nologin heligent-ngrok
fi

install -d -o heligent -g heligent -m 0750 /srv/heligent/data
install -d -o root -g root -m 0751 /etc/heligent

if [[ ! -x /srv/heligent/.venv/bin/python ]]; then
  python3 -m venv /srv/heligent/.venv
fi
/srv/heligent/.venv/bin/python -m pip install --upgrade pip wheel
/srv/heligent/.venv/bin/python -m pip install --editable /srv/heligent

if ! runuser -u postgres -- psql -tAc \
  "SELECT 1 FROM pg_roles WHERE rolname='heligent'" | grep -q 1; then
  runuser -u postgres -- createuser heligent
fi
if ! runuser -u postgres -- psql -tAc \
  "SELECT 1 FROM pg_database WHERE datname='heligent_adsb'" | grep -q 1; then
  runuser -u postgres -- createdb --owner=heligent heligent_adsb
fi

if [[ ! -e /etc/heligent/heligent.env ]]; then
  install -o root -g heligent -m 0640 \
    deploy/vps/heligent.env.example /etc/heligent/heligent.env
fi
if [[ ! -e /etc/heligent/ngrok.env ]]; then
  install -o root -g heligent-ngrok -m 0640 \
    deploy/vps/ngrok.env.example /etc/heligent/ngrok.env
fi
if [[ ! -e /etc/heligent/ngrok-traffic-policy.yml ]]; then
  install -o root -g heligent-ngrok -m 0640 \
    deploy/vps/ngrok-traffic-policy.yml.example \
    /etc/heligent/ngrok-traffic-policy.yml
fi
chown root:heligent /etc/heligent/heligent.env
chmod 0640 /etc/heligent/heligent.env
chown root:heligent-ngrok \
  /etc/heligent/ngrok.env /etc/heligent/ngrok-traffic-policy.yml
chmod 0640 /etc/heligent/ngrok.env \
  /etc/heligent/ngrok-traffic-policy.yml
if [[ -e /etc/heligent/ngrok.yml ]]; then
  chown root:heligent-ngrok /etc/heligent/ngrok.yml
  chmod 0640 /etc/heligent/ngrok.yml
fi

runuser -u heligent -- /srv/heligent/.venv/bin/heligent-migrate \
  --database-url postgresql:///heligent_adsb

install -o root -g root -m 0644 \
  deploy/vps/heligent-web.service /etc/systemd/system/heligent-web.service
install -o root -g root -m 0644 \
  deploy/vps/heligent-intelligence-api.service \
  /etc/systemd/system/heligent-intelligence-api.service
install -o root -g root -m 0644 \
  deploy/vps/heligent-ngrok.service /etc/systemd/system/heligent-ngrok.service

if [[ "${COHOST_SPROUTT}" == true ]]; then
  for service in heligent-web heligent-intelligence-api heligent-ngrok; do
    install -d -o root -g root -m 0755 \
      "/etc/systemd/system/${service}.service.d"
    install -o root -g root -m 0644 \
      "deploy/vps/cohost-sproutt/${service}.conf" \
      "/etc/systemd/system/${service}.service.d/cohost-sproutt.conf"
  done
fi
systemctl daemon-reload

echo
echo "Base installation complete. Services have not been started."
if [[ "${COHOST_SPROUTT}" == true ]]; then
  echo "Sproutt co-host resource controls were installed."
fi
echo "Next: edit /etc/heligent/*.env and ngrok-traffic-policy.yml,"
echo "install/configure ngrok and Tailscale, then enable the services."
