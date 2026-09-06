#!/usr/bin/env bash
# Codespace recovery helper for Loyverse-Bot
# Purpose: perform a safe, auditable recovery sequence that
#  - stops runaway Telegram sends across Render services,
#  - establishes one canonical primary sender (with sends disabled),
#  - runs a chronological recovery in dry-run mode for verification,
#  - prepares artifacts and checks so you can safely perform a live replay later.
#
# Milestones (explicit):
#  1) Validate Render API access (success = list of services returned).
#  2) Disable Telegram sends across all Render services (success = API calls returned 200/201).
#  3) Configure single primary service (success = PRIMARY_SENDER_ENABLED set true on chosen service; ENABLE_TELEGRAM_SENDS remains false).
#  4) Build project and run chronological recovery in dry-run (success = logs and replay-output artifacts generated).
#  5) Verification checklist (you inspect artifacts, confirm messages and receipts, confirm bot permissions).
#
# Safety rules:
#  - This script will NOT enable real Telegram sends. It preserves ENABLE_TELEGRAM_SENDS=false.
#  - This script will blank TELEGRAM tokens across services (idempotent), then optionally set the token only on the chosen primary.
#  - Do not run live replay until you confirm dry-run artifacts.
#
# Usage in Codespace:
#   1) Save file to scripts/codespace-recover.sh
#   2) chmod +x scripts/codespace-recover.sh
#   3) export RENDER_API_KEY='rnd_...'   # set securely in Codespace environment
#   4) export LOYVERSE_BOT_REPO_TOKEN='github_pat_...' # needed to clone the private app
#   5) export SERVICE_ID='srv-...'       # optional; may be entered interactively
#   6) (optional) export TELEGRAM_BOT_TOKEN='...'  # to set on the primary only
#   7) ./scripts/codespace-recover.sh
#
set -euo pipefail

# Helpers
print() { printf "%s\n" "$*"; }
die() { print "ERROR: $*"; exit 1; }

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
APP_DIR="${LOYVERSE_BOT_DIR:-$REPO_ROOT/app}"

# Milestone printer
milestone() {
  idx="$1"; msg="$2"
  print "========================================"
  print "MILESTONE $idx: $msg"
  print "========================================"
}

# 1) Validate Render API access
milestone 1 "Validate Render API access (list services)"
# Auto-load scripts/.env if the key isn't already exported
if [ -z "${RENDER_API_KEY:-}" ] && [ -f "$(dirname "$0")/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$(dirname "$0")/.env"
  set +a
fi
if [ -z "${RENDER_API_KEY:-}" ]; then
  die "RENDER_API_KEY not set. Export it and re-run. Example: export RENDER_API_KEY='rnd_...'"
fi

API_BASE="https://api.render.com/v1"
print "Calling Render API: GET /services"
resp=$(curl -sS -w "\n%{http_code}" -H "Authorization: Bearer $RENDER_API_KEY" "$API_BASE/services" || true)
status=$(echo "$resp" | tail -n1)
body=$(echo "$resp" | sed '$d')

if [ "$status" != "200" ] && [ "$status" != "201" ]; then
  print "[Render API test FAILED] HTTP $status"
  print "Response (truncated):"
  echo "$body" | head -n40
  die "Fix API key/permissions and retry."
fi

svc_count=$(echo "$body" | jq 'length')
print "[Render API test OK] Services found: $svc_count"

# Confirm existence of required scripts
for s in "$SCRIPT_DIR/disable-all-render-telegrams.sh" "$SCRIPT_DIR/configure-primary-render.sh"; do
  if [ ! -f "$s" ]; then
    die "Required script $s missing. Add it to repo and re-run."
  fi
done

# Make sure scripts are executable
chmod +x "$SCRIPT_DIR"/*.sh || true

# 2) Disable Telegram sends across all services
milestone 2 "Disable Telegram sends across all Render services (idempotent)"
read -p $'Type YES to proceed with disabling sends across all services: ' confirm_disable
if [ "$confirm_disable" = "YES" ]; then
  print "Running disable script..."
  # run with RENDER_API_KEY in environment
  if ! RENDER_API_KEY="$RENDER_API_KEY" "$SCRIPT_DIR/disable-all-render-telegrams.sh"; then
    die "Disable script failed. Inspect logs above for '[render] HTTP ...' errors."
  fi
  print "Disable step completed; tokens blanked and ENABLE_TELEGRAM_SENDS=false applied (idempotent)."
else
  print "Skipping disable step as requested by operator."
fi

# 3) Configure one primary service
milestone 3 "Configure one primary Render service (keeps sends disabled)"
if [ -z "${SERVICE_ID:-}" ]; then
  read -p $'No SERVICE_ID exported. Enter SERVICE_ID to configure as primary (leave blank to skip): ' svc_in
  SERVICE_ID="$svc_in"
fi

if [ -n "${SERVICE_ID:-}" ]; then
  read -p $'Type YES to configure '"$SERVICE_ID"' as the primary sender (keeps ENABLE_TELEGRAM_SENDS=false): ' conf_primary
  if [ "$conf_primary" = "YES" ]; then
    export SERVICE_ID
    print "Running configure-primary-render.sh for $SERVICE_ID ..."
    if ! RENDER_API_KEY="$RENDER_API_KEY" TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}" "$SCRIPT_DIR/configure-primary-render.sh" "$SERVICE_ID"; then
      die "Configure-primary script failed. Inspect logs above."
    fi
    print "Primary service configured: $SERVICE_ID (PRIMARY_SENDER_ENABLED=true). ENABLE_TELEGRAM_SENDS remains false."
  else
    print "Skipping primary configuration as requested by operator."
  fi
else
  print "No SERVICE_ID provided; primary configuration skipped."
fi

# 4) Build and run chronological recovery in dry-run mode
milestone 4 "Build project and run chronological recovery in dry-run mode"
read -p $'Type YES to install deps, build, and run chronological dry-run now: ' conf_dryrun
if [ "$conf_dryrun" = "YES" ]; then
  if [ ! -f "$APP_DIR/package.json" ]; then
    if [ -e "$APP_DIR" ]; then
      die "LOYVERSE_BOT_DIR '$APP_DIR' exists but does not contain package.json. Point LOYVERSE_BOT_DIR at a Loyverse-Bot checkout."
    fi
    command -v gh >/dev/null 2>&1 || die "Loyverse-Bot is not checked out and GitHub CLI is unavailable. Set LOYVERSE_BOT_DIR to an existing checkout."
    [ -n "${LOYVERSE_BOT_REPO_TOKEN:-}" ] || die "LOYVERSE_BOT_REPO_TOKEN is required to clone the private Loyverse-Bot repository. Export the token used by the replay workflow, or set LOYVERSE_BOT_DIR to an existing checkout."
    print "Checking out Loyverse-Bot into $APP_DIR ..."
    GH_TOKEN="$LOYVERSE_BOT_REPO_TOKEN" gh repo clone sheerwavetherapy-creator/Loyverse-Bot "$APP_DIR" || die "Could not check out Loyverse-Bot. Verify LOYVERSE_BOT_REPO_TOKEN can access sheerwavetherapy-creator/Loyverse-Bot, or set LOYVERSE_BOT_DIR to an existing checkout."
  fi

  print "Using Loyverse-Bot checkout: $APP_DIR"
  print "Installing dependencies (npm ci)..."
  npm --prefix "$APP_DIR" ci

  print "Building project (npm run build)..."
  npm --prefix "$APP_DIR" run build

  print "Running chronological recovery in dry-run mode..."
  mkdir -p "$APP_DIR/replay-output" "$APP_DIR/logs"
  : "${LOYVERSE_REPLAY_CUTOFF_RECEIPT:?Set LOYVERSE_REPLAY_CUTOFF_RECEIPT before running recovery.}"
  # Ensure safety flags are set in env for this run
  ENABLE_TELEGRAM_SENDS='false' ALLOW_HISTORICAL_RECOVERY='true' LOYVERSE_REPLAY_CUTOFF_RECEIPT="$LOYVERSE_REPLAY_CUTOFF_RECEIPT" npm --prefix "$APP_DIR" run recover:chronological -- --dry-run 2>&1 | tee "$APP_DIR/logs/chronological-dryrun-$(date +%Y%m%dT%H%M%S).log"

  print "Dry-run finished. Check $APP_DIR/logs/ for the run log and $APP_DIR/replay-output/ for any generated artifacts."
else
  print "Skipping build/dry-run step as requested by operator."
fi

# 5) Verification checklist for operator
milestone 5 "Verification checklist (operator must complete before any live replay)"
print "- Inspect the dry-run log files under logs/: ensure the receipts/messages to be replayed match expectations."
print "- Inspect replay-output/ (if present) to confirm data formatting and message payloads."
print "- On Render dashboard, confirm ONLY the chosen primary service has PRIMARY_SENDER_ENABLED=true."
print "- Confirm ENABLE_TELEGRAM_SENDS=false on all services until you are ready to enable sends."
print "- Verify the bot is a member/admin of the Telegram chat and TELEGRAM_BOT_TOKEN and CHAT ID are correct."
print "- When you are fully satisfied, run a controlled live replay (remove --dry-run) and then enable ENABLE_TELEGRAM_SENDS=true on the primary only."
print "- Monitor Telegram closely and be ready to flip ENABLE_TELEGRAM_SENDS back to false if unexpected behavior occurs."

print "Codespace recovery sequence complete (interactive). If anything failed above, copy the corresponding log block and paste it here for diagnosis."