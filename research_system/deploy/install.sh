#!/usr/bin/env bash
# Fresh-VPS bootstrap. Tested on Ubuntu 22.04 / 24.04 LTS.
#
# Usage on a brand-new VPS (logged in as a sudo-capable user):
#
#   curl -fsSL https://raw.githubusercontent.com/<your-fork>/dashboard/<branch>/research_system/deploy/install.sh | bash
#
# ...or after `git clone`:
#
#   cd dashboard
#   bash research_system/deploy/install.sh
#
# What it does:
#   1. installs Docker + Docker Compose plugin
#   2. clones (or pulls) the repo into ~/research-monitor (only if curl-piped)
#   3. prompts you for ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
#   4. writes .env, builds image, brings up scheduler + telegram + dashboard
#   5. prints SSH-tunnel command for the dashboard

set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${BLUE}▸${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

# ----- Detect repo root --------------------------------------------------
if [ -f research_system/deploy/docker-compose.yml ]; then
  REPO_ROOT="$(pwd)"
elif [ -d "${HOME}/research-monitor/research_system" ]; then
  REPO_ROOT="${HOME}/research-monitor"
  cd "$REPO_ROOT"
else
  fail "Run this from the repo root (where research_system/ lives), or git clone first."
fi
ok "Repo root: $REPO_ROOT"

# ----- Install Docker if missing ----------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker (official get.docker.com script)…"
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER" || true
  ok "Docker installed."
else
  ok "Docker already present."
fi

# ----- Ensure compose plugin --------------------------------------------
if ! docker compose version >/dev/null 2>&1; then
  log "Installing docker-compose-plugin…"
  sudo apt-get update -y
  sudo apt-get install -y docker-compose-plugin
fi
ok "Docker Compose ready: $(docker compose version)"

# ----- Collect secrets ---------------------------------------------------
ENV_FILE="$REPO_ROOT/.env"
if [ ! -f "$ENV_FILE" ]; then
  log "Creating .env"
  read -rp "ANTHROPIC_API_KEY: " ANTHROPIC_KEY
  read -rp "TELEGRAM_BOT_TOKEN (blank to skip Telegram): " TG_TOKEN || true
  read -rp "TELEGRAM_CHAT_ID   (blank to skip Telegram): " TG_CHAT  || true
  read -rp "Anthropic model [claude-sonnet-4-5]: " MODEL_OVERRIDE || true
  MODEL_OVERRIDE="${MODEL_OVERRIDE:-claude-sonnet-4-5}"
  cat > "$ENV_FILE" <<EOF
ANTHROPIC_API_KEY=${ANTHROPIC_KEY}
ANTHROPIC_MODEL=${MODEL_OVERRIDE}
TELEGRAM_BOT_TOKEN=${TG_TOKEN}
TELEGRAM_CHAT_ID=${TG_CHAT}
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
EMAIL_TO=
EOF
  chmod 600 "$ENV_FILE"
  ok ".env written (mode 600)"
else
  ok ".env already exists — keeping it."
fi

# ----- Build + start -----------------------------------------------------
COMPOSE="docker compose -f $REPO_ROOT/research_system/deploy/docker-compose.yml --env-file $ENV_FILE"
log "Building image…"
$COMPOSE build
log "Bringing services up…"
$COMPOSE up -d
sleep 3
$COMPOSE ps

# ----- Final instructions ------------------------------------------------
HOST_IP="$(curl -s ifconfig.me || echo YOUR.VPS.IP)"
cat <<EOF

$(echo -e ${GREEN}✓ Deployed.${NC})

Live services:
  • research-scheduler   — fetchers + analyser cron
  • research-telegram    — interactive phone bot
  • research-dashboard   — Streamlit UI (loopback-bound, port 8501)

View dashboard from your laptop (SSH tunnel — secure, no public exposure):

  ssh -N -L 8501:127.0.0.1:8501 $(whoami)@${HOST_IP}
  open http://localhost:8501

Useful commands on the VPS:

  $COMPOSE logs -f scheduler         # tail scheduler logs
  $COMPOSE logs -f telegram
  $COMPOSE restart                   # restart everything
  $COMPOSE down                      # stop everything
  $COMPOSE up -d --build             # rebuild after pulling new commits

Telegram bot: text it /help on your phone.
EOF
