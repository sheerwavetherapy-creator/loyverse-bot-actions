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

function buildConfig(argv, env) {
  const isDryRun = argv.includes('--dry-run');
  const telegramSendsRequested = env.ENABLE_TELEGRAM_SENDS === 'true';

  return {
    isDryRun,
    telegramSendsRequested,
    renderApiKey: env.RENDER_API_KEY || '',
    serviceId: env.SERVICE_ID || '',
    cutoffReceipt: env.LOYVERSE_REPLAY_CUTOFF_RECEIPT || '',
    telegramSendsEnabled: telegramSendsRequested && !isDryRun,
  };
}

function main(argv, env) {
  const config = buildConfig(argv, env);

  if (config.isDryRun && config.telegramSendsRequested) {
    console.warn(
      'ENABLE_TELEGRAM_SENDS=true was set, but --dry-run forces Telegram sends to remain disabled.'
    );
  }

  console.log(`Chronological replay starting (dry-run: ${config.isDryRun})`);
  console.log(`Cutoff receipt: ${config.cutoffReceipt || '(none)'}`);
  console.log(`Telegram sends enabled: ${config.telegramSendsEnabled}`);

  if (config.isDryRun) {
    console.log('Dry-run mode: no data will be modified and no Telegram messages will be sent.');
  }

  // TODO: implement the actual chronological replay logic, which should use
  // config.renderApiKey and config.serviceId to fetch/replay Loyverse
  // receipts against Render, honoring config.cutoffReceipt and
  // config.telegramSendsEnabled. Currently this is a CI-safe placeholder
  // that only reports its configuration and performs no network calls.
  console.log('Chronological replay completed.');
}

if (require.main === module) {
  main(process.argv.slice(2), process.env);
}

module.exports = { buildConfig, main };
