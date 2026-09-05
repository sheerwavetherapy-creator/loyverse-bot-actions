#!/usr/bin/env node
'use strict';

/**
 * Chronological replay/recovery script.
 *
 * Reads configuration from environment variables and CLI flags, then
 * (in non dry-run mode) would replay Loyverse receipts in chronological
 * order and forward them to Render/Telegram. When invoked with
 * `--dry-run`, no external calls are made and no Telegram sends are
 * performed regardless of `ENABLE_TELEGRAM_SENDS`.
 */

const isDryRun = process.argv.includes('--dry-run');
const telegramSendsRequested = process.env.ENABLE_TELEGRAM_SENDS === 'true';

const config = {
  renderApiKey: process.env.RENDER_API_KEY || '',
  serviceId: process.env.SERVICE_ID || '',
  cutoffReceipt: process.env.LOYVERSE_REPLAY_CUTOFF_RECEIPT || '',
  telegramSendsEnabled: telegramSendsRequested && !isDryRun,
};

if (isDryRun && telegramSendsRequested) {
  console.warn(
    'ENABLE_TELEGRAM_SENDS=true was set, but --dry-run forces Telegram sends to remain disabled.'
  );
}

function main() {
  console.log(`Chronological replay starting (dry-run: ${isDryRun})`);
  console.log(`Cutoff receipt: ${config.cutoffReceipt || '(none)'}`);
  console.log(`Telegram sends enabled: ${config.telegramSendsEnabled}`);

  if (isDryRun) {
    console.log('Dry-run mode: no data will be modified and no Telegram messages will be sent.');
  }

  // TODO: implement the actual chronological replay logic, which should use
  // config.renderApiKey and config.serviceId to fetch/replay Loyverse
  // receipts against Render, honoring config.cutoffReceipt and
  // config.telegramSendsEnabled. Currently this is a CI-safe placeholder
  // that only reports its configuration and performs no network calls.
  console.log('Chronological replay completed.');
}

main();
