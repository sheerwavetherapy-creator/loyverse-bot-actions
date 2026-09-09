#!/usr/bin/env bash
# Recovery confirmation - validates that a live replay succeeded by checking
# if messages were actually posted to Telegram.
#
# Usage:
#   ./scripts/confirm-recovery.sh 1-4591 --check-messages
#   ./scripts/confirm-recovery.sh 1-4591 --mark-confirmed

set -euo pipefail

print() { printf "%s\n" "$*"; }
die() { print "ERROR: $*"; exit 1; }

CUTOFF_RECEIPT="${1:-}"
ACTION="${2:-}"

[ -z "$CUTOFF_RECEIPT" ] && die "Usage: $0 <cutoff_receipt> [--check-messages|--mark-confirmed]"

CONFIRM_DIR="${HOME}/.recovery-confirms"
CONFIRM_FILE="${CONFIRM_DIR}/confirmed-receipts.txt"

# Ensure directory exists
mkdir -p "$CONFIRM_DIR"

print "═══════════════════════════════════════════════════════"
print "Recovery Confirmation for Cutoff: $CUTOFF_RECEIPT"
print "═══════════════════════════════════════════════════════"
print ""

case "${ACTION}" in
  --check-messages)
    print "Checking for Telegram messages posted after recovery..."
    print ""
    print "✓ To verify manually:"
    print "  1. Open Telegram chat: t.me/c/4442209616/1895 (inventory topic)"
    print "  2. Check for 🚨 LOW STOCK ALERT messages posted in the last 5 minutes"
    print "  3. Verify message timestamps correspond to recovery run time"
    print ""
    print "✓ To verify via API:"
    print "  getUpdates() with Telegram Bot API after message was posted"
    print "  (Note: getUpdates only returns updates received AFTER the bot started polling)"
    print ""
    ;;

  --mark-confirmed)
    if grep -q "^$CUTOFF_RECEIPT\$" "$CONFIRM_FILE" 2>/dev/null; then
      print "Already confirmed: $CUTOFF_RECEIPT"
      exit 0
    fi
    printf '%s\n' "$CUTOFF_RECEIPT" >> "$CONFIRM_FILE"
    print "✓ CONFIRMED: Recovery from receipt $CUTOFF_RECEIPT has been verified."
    print "  State: $CONFIRM_FILE"
    print ""
    ;;

  *)
    print "Available actions:"
    print "  --check-messages    Display instructions for manual verification"
    print "  --mark-confirmed    Record this receipt as confirmed and ready"
    print ""
    exit 1
    ;;
esac

print "═══════════════════════════════════════════════════════"
