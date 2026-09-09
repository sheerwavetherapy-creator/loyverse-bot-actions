#!/usr/bin/env bash
# Kill Telegram sends across ALL Render services (idempotent).
#
# For every service in the Render account this script:
#   1. Suspends the service first to stop any active sender process
#   2. Sets sender flags false
#   3. Masks inherited Telegram credentials with inert service-level values
#   4. Masks webhook/replay inputs so accidental resume remains blocked
#
# Required env: RENDER_API_KEY
# Safe to re-run: setting the same inert values is a no-op.

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
    print "  OK   $key (HTTP $st)"
    return 0
  fi
  print "  FAIL $key (HTTP $st)"
  echo "$resp" | sed '$d' | head -n5
  return 1
}

delete_env() {
  local sid="$1" key="$2"
  local resp st
  resp=$(curl -sS -w "\n%{http_code}" -X DELETE \
    -H "$AUTH" \
    "$API_BASE/services/$sid/env-vars/$key" || true)
  st=$(echo "$resp" | tail -n1)
  if [ "$st" = "200" ] || [ "$st" = "204" ] || [ "$st" = "404" ]; then
    return 0
  fi
  echo "$resp" | sed '$d' | head -n5
  return 1
}

suspend_service() {
  local sid="$1"
  local resp st
  resp=$(curl -sS -w "\n%{http_code}" -X POST \
    -H "$AUTH" \
    "$API_BASE/services/$sid/suspend" || true)
  st=$(echo "$resp" | tail -n1)
  if [ "$st" = "200" ] || [ "$st" = "201" ] || [ "$st" = "202" ] || [ "$st" = "204" ] || [ "$st" = "409" ]; then
    print "  OK   service suspended or already suspended (HTTP $st)"
    return 0
  fi
  print "  FAIL service suspend (HTTP $st)"
  echo "$resp" | sed '$d' | head -n5
  return 1
}

fail=0
for i in $(seq 0 $((count - 1))); do
  sid=$(echo "$body" | jq -r ".[$i].service.id")
  sname=$(echo "$body" | jq -r ".[$i].service.name")
  print "[$((i + 1))/$count] $sname ($sid)"

  svc_fail=0
  suspend_service "$sid" || svc_fail=$((svc_fail + 1))
  upsert_env "$sid" "ENABLE_TELEGRAM_SENDS" "false" || svc_fail=$((svc_fail + 1))
  upsert_env "$sid" "PRIMARY_SENDER_ENABLED" "false" || svc_fail=$((svc_fail + 1))
  upsert_env "$sid" "SEND_TELEGRAM_MESSAGE" "false" || svc_fail=$((svc_fail + 1))
  upsert_env "$sid" "TELEGRAM_KILL_SWITCH" "true" || svc_fail=$((svc_fail + 1))
  upsert_env "$sid" "TELEGRAM_BOT_TOKEN" "disabled-by-emergency-stop" || svc_fail=$((svc_fail + 1))
  upsert_env "$sid" "TELEGRAM_CHAT_ID" "0" || svc_fail=$((svc_fail + 1))
  upsert_env "$sid" "LOYVERSE_WEBHOOK_SECRET" "disabled-by-emergency-stop" || svc_fail=$((svc_fail + 1))
  upsert_env "$sid" "ALLOW_HISTORICAL_RECOVERY" "false" || svc_fail=$((svc_fail + 1))
  upsert_env "$sid" "ALLOW_HISTORICAL_POSTS" "false" || svc_fail=$((svc_fail + 1))
  upsert_env "$sid" "LOYVERSE_IGNORE_EVENTS_BEFORE" "2099-01-01T00:00:00Z" || svc_fail=$((svc_fail + 1))
  upsert_env "$sid" "LOYVERSE_WEBHOOK_IGNORE_BEFORE" "2099-01-01T00:00:00Z" || svc_fail=$((svc_fail + 1))
  upsert_env "$sid" "LOYVERSE_LIVE_START_TIME" "2099-01-01T00:00:00Z" || svc_fail=$((svc_fail + 1))

  if [ "$svc_fail" -eq 0 ]; then
    print "  OK - service suspended, Telegram masked, webhook/replay blocked"
  else
    print "  FAILED ($svc_fail var(s))"
    fail=$((fail + 1))
  fi
done

print ""
if [ "$fail" -gt 0 ]; then
  die "$fail service(s) failed to update. Check logs above."
else
  print "All $count service(s) killed: services suspended, Telegram credentials masked, webhook/replay blocked."
fi
