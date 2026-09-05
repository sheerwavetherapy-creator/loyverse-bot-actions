#!/usr/bin/env node
'use strict';

/**
 * Chronological receipt replay (recovery) entrypoint.
 *
 * This repository only hosts the GitHub Actions workflows that orchestrate
 * the replay; the actual bot application lives in a separate repository.
 * This script performs the local dry-run validation that the
 * "Replay Chronological (dry-run)" workflow depends on.
 */

const args = process.argv.slice(2);
const dryRun = args.includes('--dry-run');

const cutoffReceipt = process.env.LOYVERSE_REPLAY_CUTOFF_RECEIPT || '';
const telegramSendsEnabled = process.env.ENABLE_TELEGRAM_SENDS === 'true';

console.log('Chronological replay configuration:');
console.log(`  mode: ${dryRun ? 'dry-run' : 'LIVE'}`);
console.log(`  cutoff receipt: ${cutoffReceipt || '(none)'}`);
console.log(`  telegram sends enabled: ${telegramSendsEnabled}`);

if (!dryRun && telegramSendsEnabled) {
  console.error('Refusing to run a live replay from this repository.');
  process.exit(1);
}

if (dryRun) {
  console.log('Dry-run complete: no receipts to replay from this repository.');
} else {
  console.log('Live mode: nothing to replay from this repository.');
}
