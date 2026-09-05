#!/usr/bin/env node
/**
 * Chronological recovery/replay entry point.
 *
 * This script is intentionally conservative: unless explicitly told
 * otherwise, it never performs any network calls (Render/Telegram/Loyverse).
 * It is meant to be run with the `--dry-run` flag from CI so the
 * "Replay Chronological (dry-run)" workflow can validate the pipeline
 * without side effects.
 */

'use strict';

function parseArgs(argv) {
  const args = { dryRun: false };
  const unrecognized = [];
  for (const arg of argv) {
    if (arg === '--dry-run') {
      args.dryRun = true;
    } else {
      unrecognized.push(arg);
    }
  }
  if (unrecognized.length > 0) {
    throw new Error(`Unrecognized argument(s): ${unrecognized.join(', ')}`);
  }
  return args;
}

function main() {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (err) {
    console.error(err.message);
    process.exit(1);
  }

  const enableTelegramSends = process.env.ENABLE_TELEGRAM_SENDS === 'true';
  const cutoffReceipt = process.env.LOYVERSE_REPLAY_CUTOFF_RECEIPT || '';

  if (!args.dryRun) {
    // Safety net: this entry point should only ever be invoked with --dry-run
    // until a real, reviewed implementation is added.
    // TODO: implement the live (non-dry-run) chronological recovery/replay
    // logic and remove this restriction once it has been reviewed.
    console.error('recover:chronological currently only supports --dry-run mode.');
    process.exit(1);
  }

  console.log('Running chronological recovery in DRY-RUN mode.');
  console.log(`  ENABLE_TELEGRAM_SENDS: ${enableTelegramSends}`);
  console.log(`  LOYVERSE_REPLAY_CUTOFF_RECEIPT: ${cutoffReceipt || '(none)'}`);
  console.log('No external API calls will be made during a dry-run.');
}

module.exports = { parseArgs };

if (require.main === module) {
  main();
}
