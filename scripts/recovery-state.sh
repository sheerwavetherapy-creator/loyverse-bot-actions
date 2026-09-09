#!/usr/bin/env bash
# Recovery state management for idempotent and auditable recovery runs.
# Tracks completed recovery ranges to prevent duplicate replays.
#
# Usage:
#   source scripts/recovery-state.sh
#   recovery_state_init "1-4591"  # Initialize with cutoff receipt
#   recovery_state_mark_success   # Mark current recovery as completed
#   recovery_state_check_duplicate # Check if this cutoff was already run

set -euo pipefail

STATE_DIR="${HOME}/.recovery-state"
STATE_FILE="${STATE_DIR}/recovery-runs.jsonl"

print() { printf "%s\n" "$*"; }
die() { print "ERROR: $*"; exit 1; }

# Initialize or load recovery state
recovery_state_init() {
  local cutoff="$1"
  export RECOVERY_CUTOFF="$cutoff"
  export RECOVERY_RUN_ID="recovery-$(date -u +'%Y%m%dT%H%M%S')-${cutoff//\//-}"
  export RECOVERY_START_TIME="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  mkdir -p "$STATE_DIR"
  print "[recovery-state] Initialized: cutoff=$RECOVERY_CUTOFF, run_id=$RECOVERY_RUN_ID"
}

# Check if this recovery cutoff was already run (idempotency check)
recovery_state_check_duplicate() {
  local cutoff="${1:-${RECOVERY_CUTOFF:-}}"
  [ -z "$cutoff" ] && die "recovery_state_check_duplicate: cutoff not provided"
  
  if [ ! -f "$STATE_FILE" ]; then
    print "[recovery-state] No prior recoveries recorded."
    return 1  # Not a duplicate
  fi
  
  if grep -q "\"cutoff\":\"$cutoff\"" "$STATE_FILE"; then
    print "[recovery-state] DUPLICATE: Recovery from receipt $cutoff already completed."
    return 0  # Is a duplicate
  fi
  
  return 1  # Not a duplicate
}

# Mark the current recovery as successfully completed
recovery_state_mark_success() {
  recovery_state_mark_complete "success"
}

# Mark the current recovery as failed
recovery_state_mark_failure() {
  recovery_state_mark_complete "failure"
}

# Internal: mark recovery with status
recovery_state_mark_complete() {
  local status="${1:-success}"
  local cutoff="${RECOVERY_CUTOFF:-}"
  local run_id="${RECOVERY_RUN_ID:-unknown}"
  local start_time="${RECOVERY_START_TIME:-}"
  local end_time="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  
  [ -z "$cutoff" ] && die "recovery_state_mark_complete: RECOVERY_CUTOFF not set"
  
  mkdir -p "$STATE_DIR"
  
  # Append state line (jsonl format for easy appending and querying)
  printf '%s\n' "{\"cutoff\":\"$cutoff\",\"run_id\":\"$run_id\",\"status\":\"$status\",\"started_at\":\"$start_time\",\"completed_at\":\"$end_time\"}" >> "$STATE_FILE"
  
  print "[recovery-state] Marked $status: cutoff=$cutoff"
}

# List all prior recovery runs
recovery_state_list() {
  if [ ! -f "$STATE_FILE" ]; then
    print "[recovery-state] No recovery history."
    return
  fi
  print "[recovery-state] Recovery history:"
  nl -v1 "$STATE_FILE" | awk '{print "  [" $1 "] " $2}'
}

# Get the most recent successful recovery cutoff
recovery_state_last_successful() {
  if [ ! -f "$STATE_FILE" ]; then
    return 1
  fi
  grep '"status":"success"' "$STATE_FILE" | tail -1 | sed 's/.*"cutoff":"\([^"]*\)".*/\1/'
}

# Export functions
export -f recovery_state_init
export -f recovery_state_check_duplicate
export -f recovery_state_mark_success
export -f recovery_state_mark_failure
export -f recovery_state_list
export -f recovery_state_last_successful
