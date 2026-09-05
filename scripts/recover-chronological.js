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
  for (const arg of argv) {
    if (arg === '--dry-run') {
      args.dryRun = true;
    }
  }
  return args;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const enableTelegramSends = process.env.ENABLE_TELEGRAM_SENDS === 'true';
  const cutoffReceipt = process.env.LOYVERSE_REPLAY_CUTOFF_RECEIPT || '';

  if (!args.dryRun) {
    // Safety net: this entry point should only ever be invoked with --dry-run
    // until a real, reviewed implementation is added.
    console.error('recover:chronological currently only supports --dry-run mode.');
    process.exit(1);
  }

  console.log('Running chronological recovery in DRY-RUN mode.');
  console.log(`  ENABLE_TELEGRAM_SENDS: ${enableTelegramSends}`);
  console.log(`  LOYVERSE_REPLAY_CUTOFF_RECEIPT: ${cutoffReceipt || '(none)'}`);
  console.log('No external API calls will be made during a dry-run.');
}

main();
