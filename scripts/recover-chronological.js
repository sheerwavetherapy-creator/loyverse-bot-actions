#!/usr/bin/env node
/**
 * Chronological replay/recovery script.
 *
 * This script is intended to replay Loyverse receipts in chronological order
 * and forward them to their configured destinations (e.g. Telegram).
 *
 * It supports a `--dry-run` flag which, when present, ensures no external
 * side effects (like sending Telegram messages) are performed. This is
 * enforced regardless of other configuration to keep CI/manual dry-runs safe.
 *
 * TODO: This is currently a placeholder that only logs its inputs and does
 * not yet fetch receipts from Loyverse or forward them to Telegram. Replace
 * the body of `main()` with the real chronological replay/recovery logic.
 */

'use strict';

const args = process.argv.slice(2);
const isDryRun = args.includes('--dry-run');

const {
  LOYVERSE_REPLAY_CUTOFF_RECEIPT = '',
  ENABLE_TELEGRAM_SENDS = 'false',
} = process.env;

const telegramSendsEnabled = !isDryRun && ENABLE_TELEGRAM_SENDS === 'true';

function log(message) {
  // eslint-disable-next-line no-console
  console.log(`[recover:chronological] ${message}`);
}

async function main() {
  log(`Starting chronological recovery (dry-run: ${isDryRun})`);

  if (LOYVERSE_REPLAY_CUTOFF_RECEIPT) {
    log(`Using cutoff receipt: ${LOYVERSE_REPLAY_CUTOFF_RECEIPT}`);
  } else {
    log('No cutoff receipt provided; processing from the beginning.');
  }

  if (isDryRun) {
    log('Dry-run mode: no messages will be sent and no external state will be modified.');
  } else if (!telegramSendsEnabled) {
    log('Telegram sends are disabled (ENABLE_TELEGRAM_SENDS is not "true").');
  }

  log('Chronological recovery finished successfully.');
}

main().catch((error) => {
  // eslint-disable-next-line no-console
  console.error('[recover:chronological] Failed:', error);
  process.exitCode = 1;
});
