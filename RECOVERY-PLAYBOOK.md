# Recovery Playbook: Safe, Auditable Replays

This playbook describes how to safely perform and track historical recovery runs using the new state management and confirmation tools.

## Overview

The recovery system now provides:
- **State tracking**: Each recovery run is recorded in `~/.recovery-state/recovery-runs.jsonl`
- **Idempotency**: Duplicate recovery attempts for the same receipt range are prevented
- **Confirmation**: Manual verification step before marking recovery as complete

## Workflow

### Step 1: Check Prior Recovery History

```bash
source scripts/recovery-state.sh
recovery_state_list
```

Output example:
```
[recovery-state] Recovery history:
  [1] {"cutoff":"1-4591","run_id":"recovery-20260909T092036-1-4591","status":"success","started_at":"2026-09-09T08:23:09Z","completed_at":"2026-09-09T08:28:44Z"}
```

### Step 2: Initialize New Recovery Run

```bash
source scripts/recovery-state.sh
recovery_state_init "1-4591"  # Set the cutoff receipt

# Check if this cutoff was already recovered
recovery_state_check_duplicate
if [ $? -eq 0 ]; then
  echo "Already recovered; skipping."
  exit 0
fi
```

### Step 3: Trigger Live Replay Workflow

Go to: https://github.com/sheerwavetherapy-creator/loyverse-bot-actions/actions/workflows/replay-chronological-live.yml

Or trigger via CLI (requires elevated token permissions):

```bash
gh workflow run replay-chronological-live.yml \
  -f cutoff_receipt="1-4591" \
  -f confirm="SEND" \
  --repo sheerwavetherapy-creator/loyverse-bot-actions
```

**Wait for workflow to complete** (~3–5 minutes)

### Step 4: Verify Telegram Messages

Run the confirmation check:

```bash
./scripts/confirm-recovery.sh 1-4591 --check-messages
```

This displays instructions for manual verification:

1. Open Telegram: [Payinpayout Topic](t.me/c/4442209616/????)
2. Look for transaction messages posted by the Loyverse-Bot in the last 5 minutes
3. Verify message timestamps and content match the recovery receipt range
4. Check for any error messages or delivery failures

### Step 5: Mark Recovery as Confirmed

Once you've verified the messages in Telegram:

```bash
source scripts/recovery-state.sh
recovery_state_mark_success  # (already initialized in Step 2)

# Or manually confirm via the confirmation script:
./scripts/confirm-recovery.sh 1-4591 --mark-confirmed
```

### Step 6: Proceed to Next Recovery Batch

If the recovery range is large, break it into batches:

```bash
for batch in "1-100" "101-200" "201-300"; do
  source scripts/recovery-state.sh
  recovery_state_init "$batch"
  
  if recovery_state_check_duplicate; then
    echo "Batch $batch already recovered; skipping."
    continue
  fi
  
  # Trigger workflow (manual or via API)
  # Wait for completion
  # Verify Telegram messages
  
  recovery_state_mark_success
done
```

## Files

- `~/.recovery-state/recovery-runs.jsonl` — Complete audit trail of all recovery runs
- `~/.recovery-confirms/confirmed-receipts.txt` — List of confirmed receipt ranges

## Troubleshooting

**Recovery shows as duplicate but I want to re-run:**

Remove the entry from the state file:
```bash
grep -v '"cutoff":"1-4591"' ~/.recovery-state/recovery-runs.jsonl > /tmp/filtered.jsonl
mv /tmp/filtered.jsonl ~/.recovery-state/recovery-runs.jsonl
```

**No Telegram messages received:**

Check the GitHub workflow logs:
```bash
gh run view <run-id> --repo sheerwavetherapy-creator/loyverse-bot-actions --log
```

Look for `[ERROR]` or `[SKIP]` messages in the step output.

**Need to abort a recovery run:**

The workflow can be cancelled from the GitHub Actions UI. The state file won't be updated until the recovery is marked as complete.
