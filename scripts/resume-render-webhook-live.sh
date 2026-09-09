#!/usr/bin/env bash
# Resume the primary Render service with a startup drain before live sends are enabled.

set -euo pipefail

print() { printf "%s\n" "$*"; }
die() { print "ERROR: $*"; exit 1; }

require_env() {
  local key="$1"
  [ -n "${!key:-}" ] || die "$key is required."
}

require_env "RENDER_API_KEY"
require_env "SERVICE_ID"
WARMUP_SECONDS="${WARMUP_SECONDS:-300}"
case "$WARMUP_SECONDS" in
  ''|*[!0-9]*) die "WARMUP_SECONDS must be a whole number of seconds." ;;
esac

API_BASE="https://api.render.com/v1"
AUTH="Authorization: Bearer $RENDER_API_KEY"
CT="Content-Type: application/json"

render_body=""
render_status=""

render_request() {
  local method="$1" path="$2" data="${3:-}"
  local args=(-sS -w "\n%{http_code}" -X "$method" -H "$AUTH")

  if [ -n "$data" ]; then
    args+=(-H "$CT" -d "$data")
  fi

  local resp
  resp=$(curl "${args[@]}" "$API_BASE$path" || true)
  render_status=$(echo "$resp" | tail -n1)
  render_body=$(echo "$resp" | sed '$d')
}

upsert_env() {
  local key="$1" value="$2"
  local payload
  payload=$(jq -nc --arg value "$value" '{value: $value}')

  render_request "PUT" "/services/$SERVICE_ID/env-vars/$key" "$payload"
  if [ "$render_status" = "200" ] || [ "$render_status" = "201" ]; then
    print "  OK   $key (HTTP $render_status)"
    return 0
  fi

  print "  FAIL $key (HTTP $render_status)"
  echo "$render_body" | head -n5
  return 1
}

delete_env() {
  local key="$1"

  render_request "DELETE" "/services/$SERVICE_ID/env-vars/$key"
  if [ "$render_status" = "200" ] || [ "$render_status" = "204" ] || [ "$render_status" = "404" ]; then
    print "  OK   $key removed or absent (HTTP $render_status)"
    return 0
  fi

  print "  WARN $key delete returned HTTP $render_status"
  echo "$render_body" | head -n5
  return 0
}

trigger_deploy() {
  local label="$1"

  print "$label"
  render_request "POST" "/services/$SERVICE_ID/deploys" '{"clearCache":"do_not_clear"}'
  case "$render_status" in
    200|201|202)
      print "  OK   deploy requested (HTTP $render_status)"
      ;;
    *)
      print "[render] HTTP $render_status"
      echo "$render_body" | head -n10
      die "Failed to trigger deploy."
      ;;
  esac
}

print "Verifying Render service $SERVICE_ID ..."
render_request "GET" "/services/$SERVICE_ID"
if [ "$render_status" != "200" ]; then
  print "[render] HTTP $render_status"
  echo "$render_body" | head -n10
  die "Service $SERVICE_ID not found or API error."
fi

service_name=$(echo "$render_body" | jq -r '.name // .service.name // "unknown"')
print "Service found: $service_name"

fail=0
guard_started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

print "Phase 1: starting $service_name with Telegram sends disabled for backlog drain ..."
upsert_env "PRIMARY_SENDER_ENABLED" "false" || fail=$((fail + 1))
upsert_env "ENABLE_TELEGRAM_SENDS" "false" || fail=$((fail + 1))
upsert_env "ALLOW_HISTORICAL_RECOVERY" "false" || fail=$((fail + 1))
upsert_env "ALLOW_HISTORICAL_POSTS" "false" || fail=$((fail + 1))
upsert_env "RENDER_STARTUP_GUARD_STARTED_AT" "$guard_started_at" || fail=$((fail + 1))
upsert_env "LOYVERSE_LIVE_START_TIME" "$guard_started_at" || fail=$((fail + 1))
upsert_env "LOYVERSE_IGNORE_EVENTS_BEFORE" "$guard_started_at" || fail=$((fail + 1))
upsert_env "LOYVERSE_WEBHOOK_IGNORE_BEFORE" "$guard_started_at" || fail=$((fail + 1))
delete_env "TELEGRAM_BOT_TOKEN"
delete_env "TELEGRAM_CHAT_ID"

if [ "$fail" -gt 0 ]; then
  die "$fail phase 1 env var(s) failed to update."
fi

if [ -n "${LOYVERSE_WEBHOOK_SECRET:-}" ]; then
  upsert_env "LOYVERSE_WEBHOOK_SECRET" "$LOYVERSE_WEBHOOK_SECRET" || fail=$((fail + 1))
else
  print "LOYVERSE_WEBHOOK_SECRET not provided; leaving the existing Render value unchanged."
fi

if [ "$fail" -gt 0 ]; then
  die "$fail env var(s) failed to update."
fi

print "Resuming Render service ..."
render_request "POST" "/services/$SERVICE_ID/resume"
case "$render_status" in
  200|201|202|204)
    print "  OK   resume requested (HTTP $render_status)"
    ;;
  400|409)
    print "  INFO resume returned HTTP $render_status; service may already be active."
    echo "$render_body" | head -n5
    ;;
  *)
    print "[render] HTTP $render_status"
    echo "$render_body" | head -n10
    die "Failed to resume service."
    ;;
esac

trigger_deploy "Triggering guarded startup deploy with sends disabled ..."

if [ "$WARMUP_SECONDS" -gt 0 ]; then
  print "Waiting ${WARMUP_SECONDS}s for startup backlog to drain with sends disabled ..."
  sleep "$WARMUP_SECONDS"
fi

require_env "TELEGRAM_BOT_TOKEN"
require_env "TELEGRAM_CHAT_ID"

fail=0
print "Phase 2: enabling live webhook Telegram sends after guarded startup drain ..."
upsert_env "TELEGRAM_BOT_TOKEN" "$TELEGRAM_BOT_TOKEN" || fail=$((fail + 1))
upsert_env "TELEGRAM_CHAT_ID" "$TELEGRAM_CHAT_ID" || fail=$((fail + 1))
upsert_env "PRIMARY_SENDER_ENABLED" "true" || fail=$((fail + 1))
upsert_env "ENABLE_TELEGRAM_SENDS" "true" || fail=$((fail + 1))

if [ "$fail" -gt 0 ]; then
  die "$fail phase 2 env var(s) failed to update."
fi

trigger_deploy "Triggering live sender deploy after startup drain ..."

print "Live webhook sender enabled for $service_name after guarded startup drain from $guard_started_at."
