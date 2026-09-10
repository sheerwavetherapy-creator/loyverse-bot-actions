#!/usr/bin/env bash

set -euo pipefail

print() { printf "%s\n" "$*"; }
die() { print "ERROR: $*"; exit 1; }

require_env() {
  local key="$1"
  [ -n "${!key:-}" ] || die "$key is required."
}

require_env RENDER_API_KEY
require_env SERVICE_ID
require_env LOYVERSE_REPLAY_CUTOFF_RECEIPT

WARMUP_SECONDS="${WARMUP_SECONDS:-60}"
case "$WARMUP_SECONDS" in
  ''|*[!0-9]*) die "WARMUP_SECONDS must be a whole number of seconds." ;;
esac

API_BASE="https://api.render.com/v1"
AUTH="Authorization: Bearer $RENDER_API_KEY"
CT="Content-Type: application/json"

render_request() {
  local method="$1" path="$2" data="${3:-}"
  local args=(-sS -w "\n%{http_code}" -X "$method" -H "$AUTH")
  [ -n "$data" ] && args+=(-H "$CT" -d "$data")
  local response
  response=$(curl "${args[@]}" "$API_BASE$path" || true)
  RENDER_STATUS=$(printf '%s\n' "$response" | tail -n1)
  RENDER_BODY=$(printf '%s\n' "$response" | sed '$d')
}

upsert_env() {
  local key="$1" value="$2"
  render_request PUT "/services/$SERVICE_ID/env-vars/$key" "$(jq -nc --arg value "$value" '{value: $value}')"
  case "$RENDER_STATUS" in
    200|201) print "  OK   $key" ;;
    *) print "  FAIL $key (HTTP $RENDER_STATUS)"; printf '%s\n' "$RENDER_BODY" | head -n5; return 1 ;;
  esac
}

trigger_deploy() {
  render_request POST "/services/$SERVICE_ID/deploys" '{"clearCache":"do_not_clear"}'
  case "$RENDER_STATUS" in
    200|201|202) print "  OK   deploy requested" ;;
    *) print "  FAIL deploy (HTTP $RENDER_STATUS)"; printf '%s\n' "$RENDER_BODY" | head -n10; return 1 ;;
  esac
}

render_request GET "/services/$SERVICE_ID"
[ "$RENDER_STATUS" = "200" ] || die "Service $SERVICE_ID was not found (HTTP $RENDER_STATUS)."
SERVICE_NAME=$(printf '%s\n' "$RENDER_BODY" | jq -r '.name // .service.name // "unknown"')
print "Preparing persistent live webhook for $SERVICE_NAME after $LOYVERSE_REPLAY_CUTOFF_RECEIPT"

STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
print "Phase 1: guarded startup with Telegram sends disabled"
upsert_env PRIMARY_SENDER_ENABLED false
upsert_env ENABLE_TELEGRAM_SENDS false
upsert_env TELEGRAM_BOT_TOKEN disabled-by-startup-guard
upsert_env TELEGRAM_CHAT_ID 0
upsert_env ALLOW_HISTORICAL_RECOVERY false
upsert_env ALLOW_HISTORICAL_POSTS false
upsert_env LOYVERSE_REPLAY_CUTOFF_RECEIPT "$LOYVERSE_REPLAY_CUTOFF_RECEIPT"
upsert_env RENDER_STARTUP_GUARD_STARTED_AT "$STARTED_AT"
upsert_env LOYVERSE_LIVE_START_TIME "$STARTED_AT"
upsert_env LOYVERSE_IGNORE_EVENTS_BEFORE "$STARTED_AT"
upsert_env LOYVERSE_WEBHOOK_IGNORE_BEFORE "$STARTED_AT"

render_request POST "/services/$SERVICE_ID/resume"
case "$RENDER_STATUS" in
  200|201|202|204|409) print "  OK   resume requested (HTTP $RENDER_STATUS)" ;;
  *) print "  FAIL resume (HTTP $RENDER_STATUS)"; printf '%s\n' "$RENDER_BODY" | head -n10; exit 1 ;;
esac

if [ "$WARMUP_SECONDS" -gt 0 ]; then
  print "Waiting ${WARMUP_SECONDS}s for guarded startup"
  sleep "$WARMUP_SECONDS"
fi

render_request GET "/services/$SERVICE_ID"
[ "$RENDER_STATUS" = "200" ] || die "Could not verify service after resume (HTTP $RENDER_STATUS)."
SERVICE_SUSPENDED=$(printf '%s\n' "$RENDER_BODY" | jq -r '.suspended // true')
[ "$SERVICE_SUSPENDED" = "false" ] || die "Service did not remain active after guarded startup."

print "Phase 2: enabling persistent live webhook sends"
require_env TELEGRAM_BOT_TOKEN
require_env TELEGRAM_CHAT_ID
upsert_env TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
upsert_env TELEGRAM_CHAT_ID "$TELEGRAM_CHAT_ID"
upsert_env PRIMARY_SENDER_ENABLED true
upsert_env ENABLE_TELEGRAM_SENDS true
trigger_deploy
print "Persistent live webhook enabled for $SERVICE_NAME after $LOYVERSE_REPLAY_CUTOFF_RECEIPT"