#!/usr/bin/env bash
# Configure a single Render service as the canonical primary Telegram sender.
#
# What it does:
#   1. Sets PRIMARY_SENDER_ENABLED=true  on the chosen service
#   2. Keeps ENABLE_TELEGRAM_SENDS=false on that service (safety)
#   3. Optionally sets TELEGRAM_BOT_TOKEN on that service (if provided)
#
# Required env: RENDER_API_KEY
# Usage:        ./scripts/configure-primary-render.sh <SERVICE_ID>
#      or:      SERVICE_ID=srv-... ./scripts/configure-primary-render.sh

set -euo pipefail

print() { printf "%s\n" "$*"; }
die() { print "ERROR: $*"; exit 1; }

[ -z "${RENDER_API_KEY:-}" ] && die "RENDER_API_KEY not set. Export it or put it in scripts/.env"

# Accept SERVICE_ID as $1 or from env
SERVICE_ID="${1:-${SERVICE_ID:-}}"
[ -z "$SERVICE_ID" ] && die "No SERVICE_ID provided. Pass as arg or export SERVICE_ID."

API_BASE="https://api.render.com/v1"
AUTH="Authorization: Bearer $RENDER_API_KEY"
CT="Content-Type: application/json"

# Verify the service exists
print "Verifying service $SERVICE_ID ..."
resp=$(curl -sS -w "\n%{http_code}" -H "$AUTH" "$API_BASE/services/$SERVICE_ID" || true)
status=$(echo "$resp" | tail -n1)
if [ "$status" != "200" ]; then
  print "[render] HTTP $status"
  echo "$resp" | sed '$d' | head -n10
  die "Service $SERVICE_ID not found or API error."
fi
sname=$(echo "$resp" | sed '$d' | jq -r '.name // .service.name // "unknown"')
print "Service found: $sname"

# Update env vars individually (PUT /env-vars/:key) so we MERGE rather than replace
# the whole env set. A bulk PUT of a partial list would wipe every other variable —
# that is exactly what previously caused deploys to crash on missing LOYVERSE_* / TELEGRAM_*.
upsert_env() {
  local key="$1" value="$2"
  local resp st
  resp=$(curl -sS -w "\n%{http_code}" -X PUT \
    -H "$AUTH" -H "$CT" \
    -d "{\"value\":\"$value\"}" \
    "$API_BASE/services/$SERVICE_ID/env-vars/$key" || true)
  st=$(echo "$resp" | tail -n1)
  if [ "$st" = "200" ] || [ "$st" = "201" ]; then
    print "  OK   $key (HTTP $st)"
  else
    print "  FAIL $key (HTTP $st)"
    echo "$resp" | sed '$d' | head -n5
    fail=$((fail + 1))
  fi
}

fail=0
print "Updating env vars on $sname ($SERVICE_ID) ..."
upsert_env "PRIMARY_SENDER_ENABLED" "true"
upsert_env "ENABLE_TELEGRAM_SENDS" "false"

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  upsert_env "TELEGRAM_BOT_TOKEN" "$TELEGRAM_BOT_TOKEN"
else
  print "TELEGRAM_BOT_TOKEN not provided — token will NOT be changed."
fi

print ""
if [ "$fail" -gt 0 ]; then
  die "$fail env var(s) failed to update. Check logs above."
else
  print "Primary configured: PRIMARY_SENDER_ENABLED=true, ENABLE_TELEGRAM_SENDS=false"
fi
