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

# Build env-var payload
payload='[
  {"key":"PRIMARY_SENDER_ENABLED","value":"true"},
  {"key":"ENABLE_TELEGRAM_SENDS","value":"false"}
]'

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  payload=$(echo "$payload" | jq --arg t "$TELEGRAM_BOT_TOKEN" '. + [{"key":"TELEGRAM_BOT_TOKEN","value":$t}]')
  print "TELEGRAM_BOT_TOKEN will be set on this service."
else
  print "TELEGRAM_BOT_TOKEN not provided — token will NOT be changed."
fi

print "Updating env vars on $sname ($SERVICE_ID) ..."
r=$(curl -sS -w "\n%{http_code}" -X PUT \
  -H "$AUTH" -H "$CT" \
  -d "$payload" \
  "$API_BASE/services/$SERVICE_ID/env-vars" || true)
st=$(echo "$r" | tail -n1)

if [ "$st" = "200" ] || [ "$st" = "201" ]; then
  print "OK (HTTP $st)"
  print ""
  print "Primary configured: PRIMARY_SENDER_ENABLED=true, ENABLE_TELEGRAM_SENDS=false"
else
  print "[render] HTTP $st — FAILED"
  echo "$r" | sed '$d' | head -n10
  die "Failed to update env vars on $SERVICE_ID."
fi
