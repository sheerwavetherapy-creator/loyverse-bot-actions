#!/usr/bin/env bash
# Disable Telegram sends across ALL Render services (idempotent).
#
# For every service in the Render account this script:
#   1. Sets ENABLE_TELEGRAM_SENDS=false
#   2. Blanks TELEGRAM_BOT_TOKEN
#   3. Blanks TELEGRAM_CHAT_ID
#
# Required env: RENDER_API_KEY
# Safe to re-run: setting the same value is a no-op.

set -euo pipefail

print() { printf "%s\n" "$*"; }
die() { print "ERROR: $*"; exit 1; }

[ -z "${RENDER_API_KEY:-}" ] && die "RENDER_API_KEY not set. Export it or put it in scripts/.env"

API_BASE="https://api.render.com/v1"
AUTH="Authorization: Bearer $RENDER_API_KEY"
CT="Content-Type: application/json"

print "Fetching all Render services..."
resp=$(curl -sS -w "\n%{http_code}" -H "$AUTH" "$API_BASE/services?limit=100" || true)
status=$(echo "$resp" | tail -n1)
body=$(echo "$resp" | sed '$d')

if [ "$status" != "200" ]; then
  print "[render] HTTP $status"
  echo "$body" | head -n20
  die "Failed to list services."
fi

count=$(echo "$body" | jq 'length')
print "Found $count service(s). Disabling Telegram sends on each..."
print ""

# Update env vars individually (PUT /env-vars/:key) to MERGE, not replace.
# A bulk PUT of a partial list would wipe every other variable on the service.
upsert_env() {
  local sid="$1" key="$2" value="$3"
  local resp st
  resp=$(curl -sS -w "\n%{http_code}" -X PUT \
    -H "$AUTH" -H "$CT" \
    -d "{\"value\":\"$value\"}" \
    "$API_BASE/services/$sid/env-vars/$key" || true)
  st=$(echo "$resp" | tail -n1)
  if [ "$st" = "200" ] || [ "$st" = "201" ]; then
    return 0
  fi
  echo "$resp" | sed '$d' | head -n5
  return 1
}

fail=0
for i in $(seq 0 $((count - 1))); do
  sid=$(echo "$body" | jq -r ".[$i].service.id")
  sname=$(echo "$body" | jq -r ".[$i].service.name")
  print "[$((i + 1))/$count] $sname ($sid)"

  svc_fail=0
  upsert_env "$sid" "ENABLE_TELEGRAM_SENDS" "false" || svc_fail=$((svc_fail + 1))
  upsert_env "$sid" "TELEGRAM_BOT_TOKEN" "" || svc_fail=$((svc_fail + 1))
  upsert_env "$sid" "TELEGRAM_CHAT_ID" "" || svc_fail=$((svc_fail + 1))

  if [ "$svc_fail" -eq 0 ]; then
    print "  OK"
  else
    print "  FAILED ($svc_fail var(s))"
    fail=$((fail + 1))
  fi
done

print ""
if [ "$fail" -gt 0 ]; then
  die "$fail service(s) failed to update. Check logs above."
else
  print "All $count service(s) updated: ENABLE_TELEGRAM_SENDS=false, tokens blanked."
fi
