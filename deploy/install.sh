#!/usr/bin/env bash
# BidCraft on-premise installer for Ubuntu 24.04 LTS (no Docker).
# Run as root from the unpacked project directory:  sudo bash deploy/install.sh
#
# What it does: installs PostgreSQL 16 + pgvector, nginx, Python, .NET 8, OCR, LibreOffice and fonts;
# creates the bidcraft user, database and directories; installs the three services under systemd;
# and serves the web app through nginx. Re-running upgrades the code and keeps your data and config.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/bidcraft
FILES=/srv/bidcraft/files
ETC=/etc/bidcraft
ENV_FILE=$ETC/bidcraft.env
SITE_CONF=$ETC/site.conf      # the host and port of the first install, used again on every re-run
[ -f $SITE_CONF ] && . $SITE_CONF
# Address users open in the browser (IP or DNS name).
HOST="${BIDCRAFT_HOST:-${SAVED_HOST:-192.168.2.141}}"
# HTTPS port. 8443 because Sampark, TenderPilot and other sites own 80/443 on 192.168.2.141; with a port
# other than 443 the port-80 redirect is left out. BIDCRAFT_HTTPS_PORT=443 only on a server BidCraft has to itself.
PORT="${BIDCRAFT_HTTPS_PORT:-${SAVED_PORT:-8443}}"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)."; exit 1; }

log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  postgresql-16 postgresql-16-pgvector nginx rsync curl openssl ca-certificates \
  python3 python3-venv python3-dev build-essential libpq-dev \
  libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi-dev \
  fonts-noto-core fonts-noto-ui-core fonts-dejavu-core \
  tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin poppler-utils ocrmypdf ghostscript \
  libreoffice-writer libreoffice-calc \
  dotnet-sdk-8.0

log "Creating user and directories"
id -u bidcraft >/dev/null 2>&1 || useradd --system --home-dir $APP --shell /usr/sbin/nologin bidcraft
mkdir -p $APP/api $APP/ai-service $APP/web $APP/models $FILES $ETC/tls
chown -R bidcraft:bidcraft $APP $FILES

log "Preparing the database (peer authentication, no password)"
systemctl enable --now postgresql
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='bidcraft'" | grep -q 1 || sudo -u postgres createuser bidcraft
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='bidcraft'" | grep -q 1 || sudo -u postgres createdb -O bidcraft bidcraft
sudo -u postgres psql -d bidcraft -qc "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pgcrypto;"
# root opens the file (the source may be in a home directory the bidcraft user cannot read); any error stops
# the install, so new code never runs on an old schema. The worker is stopped first: the schema's ALTER and
# CREATE INDEX statements otherwise deadlock with a document it is saving (it resumes interrupted jobs on start).
systemctl stop bidcraft-worker 2>/dev/null || true
sudo -u bidcraft psql -v ON_ERROR_STOP=1 -d bidcraft -q < "$SRC/db/schema.sql"

log "Installing the AI service and worker (Python)"
rsync -a --delete --exclude .venv --exclude '__pycache__' "$SRC/ai-service/" $APP/ai-service/
# The outbound data policy: the package's copy, unless this server has its own in /etc/bidcraft (edit that one;
# copy it from /opt/bidcraft/ai-service/config/redaction_policy.yaml first). Deploys never overwrite it.
if [ -f $ETC/redaction_policy.yaml ] && [ -f $ENV_FILE ]; then
  sed -i "s|^POLICY_PATH=.*|POLICY_PATH=$ETC/redaction_policy.yaml|" $ENV_FILE
  echo "Using this server's outbound policy: $ETC/redaction_policy.yaml"
fi
[ -x $APP/ai-service/.venv/bin/python ] || python3 -m venv $APP/ai-service/.venv
$APP/ai-service/.venv/bin/pip install --quiet --upgrade pip wheel
$APP/ai-service/.venv/bin/pip install --quiet -r $APP/ai-service/requirements.txt
chown -R bidcraft:bidcraft $APP/ai-service

log "Downloading the local embedding model (one-time, ~130 MB)"
sudo -u bidcraft $APP/ai-service/.venv/bin/python - <<'PY'
from fastembed import TextEmbedding
TextEmbedding("BAAI/bge-small-en-v1.5", cache_dir="/opt/bidcraft/models")
print("embedding model ready")
PY

log "Building and installing the API (.NET)"
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
# Built into a staging folder first: a failed build leaves the running API alone. The running API keeps its
# files open, so it is stopped only for the swap; it is started again below.
rm -rf $APP/api.next
dotnet publish "$SRC/api/BidCraft.Api.csproj" -c Release -o $APP/api.next
systemctl stop bidcraft-api 2>/dev/null || true
rsync -a --delete $APP/api.next/ $APP/api/
rm -rf $APP/api.next
chown -R bidcraft:bidcraft $APP/api

log "Installing the web app"
if [ -d "$SRC/web/dist/bidcraft-web/browser" ]; then
  rsync -a --delete "$SRC/web/dist/bidcraft-web/browser/" $APP/web/
else
  NODE_MAJOR=$(node -v 2>/dev/null | sed 's/v\([0-9]*\).*/\1/' || echo 0)
  if [ "${NODE_MAJOR:-0}" -ge 20 ]; then
    (cd "$SRC/web" && npm ci --no-audit --no-fund && npx ng build)
    rsync -a --delete "$SRC/web/dist/bidcraft-web/browser/" $APP/web/
  else
    echo "No prebuilt web app found and Node.js 20+ is not installed."
    echo "Build it on a machine with Node 22 (cd web && npm ci && npx ng build) and copy web/dist here, then re-run."
    exit 1
  fi
fi

log "Configuration"
if [ ! -f $ENV_FILE ]; then
  cp "$SRC/deploy/bidcraft.env.example" $ENV_FILE
  TOKEN=$(openssl rand -hex 32); JWT=$(openssl rand -hex 32); VAULT=$(openssl rand -hex 32); ADMIN=$(openssl rand -base64 12)
  sed -i "s|^Ai__InternalToken=.*|Ai__InternalToken=$TOKEN|; s|^AI_INTERNAL_TOKEN=.*|AI_INTERNAL_TOKEN=$TOKEN|; \
          s|^Jwt__Key=.*|Jwt__Key=$JWT|; s|^VAULT_KEY=.*|VAULT_KEY=$VAULT|; s|^Admin__Password=.*|Admin__Password=$ADMIN|" $ENV_FILE
  echo "Created $ENV_FILE with generated secrets. First admin password: $ADMIN"
  echo "Set OPENAI_API_KEY, OPENAI_MODEL, Admin__Email and COMPANY_* in $ENV_FILE, then: systemctl restart bidcraft-api bidcraft-ai bidcraft-worker"
fi
chown root:bidcraft $ENV_FILE; chmod 640 $ENV_FILE

if [ ! -f $ETC/tls/fullchain.pem ]; then
  if [[ $HOST =~ ^[0-9.]+$ ]]; then SAN="IP:$HOST"; else SAN="DNS:$HOST"; fi
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=$HOST" -addext "subjectAltName=$SAN" \
    -keyout $ETC/tls/privkey.pem -out $ETC/tls/fullchain.pem >/dev/null 2>&1
  echo "Installed a self-signed TLS certificate in $ETC/tls; replace it with your internal CA certificate."
fi

log "Services"
cp "$SRC"/deploy/systemd/bidcraft-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now bidcraft-api bidcraft-ai bidcraft-worker
systemctl restart bidcraft-api bidcraft-ai bidcraft-worker

SITE=/etc/nginx/sites-available/bidcraft
[ -f $SITE ] && cp $SITE $SITE.previous
if [ "$PORT" = 443 ]; then
  sed "s|__BIDCRAFT_HOST__|$HOST|g; s|__BIDCRAFT_PORT__|$PORT|g" "$SRC/deploy/nginx/bidcraft.conf" > $SITE
  rm -f /etc/nginx/sites-enabled/default
else
  sed "/^# --- http-redirect/,/^# --- end http-redirect/d; s|__BIDCRAFT_HOST__|$HOST|g; s|__BIDCRAFT_PORT__|$PORT|g" \
    "$SRC/deploy/nginx/bidcraft.conf" > $SITE
  if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then ufw allow "$PORT/tcp"; fi
fi
ln -sf $SITE /etc/nginx/sites-enabled/bidcraft
# nginx serves other sites on this server: a site that fails the test is never left in place
if ! nginx -t; then
  if [ -f $SITE.previous ]; then cp $SITE.previous $SITE; else rm -f /etc/nginx/sites-enabled/bidcraft; fi
  echo "The new nginx site failed 'nginx -t'; the previous one was kept. Nothing was reloaded."
  exit 1
fi
systemctl reload nginx
printf 'SAVED_HOST=%s\nSAVED_PORT=%s\n' "$HOST" "$PORT" > $SITE_CONF

log "Done"
[ "$PORT" = 443 ] && URL="https://$HOST/" || URL="https://$HOST:$PORT/"
echo "Open $URL and sign in with Admin__Email from $ENV_FILE."
echo "Check health:  curl -k ${URL}api/health ; curl http://127.0.0.1:8001/health"
echo "Logs:          journalctl -u bidcraft-worker -f"
