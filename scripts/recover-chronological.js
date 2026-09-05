#!/usr/bin/env node
'use strict';

/**
 * Chronological replay/recovery script.
 *
 * Safety rules:
 *   - This script only performs real actions (e.g. sending Telegram
 *     messages) when it is NOT invoked with --dry-run AND
 *     ENABLE_TELEGRAM_SENDS is explicitly set to "true".
 *   - Any other combination results in a dry-run: no external side
 *     effects are performed, and a summary of what *would* have
 *     happened is written to replay-output/dry-run-summary.json.
 */

const fs = require('fs');
const path = require('path');

function parseArgs(argv) {
  return {
    dryRun: argv.includes('--dry-run'),
  };
}

function main() {
  const { dryRun } = parseArgs(process.argv.slice(2));
  const enableTelegramSends = process.env.ENABLE_TELEGRAM_SENDS === 'true';
  const cutoffReceipt = process.env.LOYVERSE_REPLAY_CUTOFF_RECEIPT || '';

  // Effective dry-run: either the caller explicitly passed --dry-run, OR
  // ENABLE_TELEGRAM_SENDS was not explicitly set to "true". Either
  // condition alone is enough to force safe (no side-effect) behavior.
  const isEffectiveDryRun = dryRun || !enableTelegramSends;

  const summary = {
    timestamp: new Date().toISOString(),
    dryRun,
    enableTelegramSends,
    cutoffReceipt,
    mode: isEffectiveDryRun ? 'dry-run' : 'live',
    message: isEffectiveDryRun
      ? 'Dry-run mode: no Telegram messages were sent and no external services were mutated.'
      : 'Live mode: recovery would run with side effects.',
  };

  const outputDir = path.join(process.cwd(), 'replay-output');
  fs.mkdirSync(outputDir, { recursive: true });
  const outputFile = path.join(outputDir, 'dry-run-summary.json');
  fs.writeFileSync(outputFile, JSON.stringify(summary, null, 2));

  console.log(`Chronological recovery run (${summary.mode}).`);
  console.log(`Summary written to ${path.relative(process.cwd(), outputFile)}`);

  if (!isEffectiveDryRun) {
    // TODO: Live recovery (actually sending Telegram messages / mutating
    // external services) is intentionally NOT implemented yet. Until a
    // reviewed implementation exists, always fail closed here rather than
    // silently doing nothing.
    console.error(
      'Refusing to perform live recovery: no live implementation exists yet.'
    );
    process.exitCode = 1;
  }
}

main();
