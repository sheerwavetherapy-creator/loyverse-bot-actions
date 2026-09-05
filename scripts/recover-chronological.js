#!/usr/bin/env node
'use strict';

function buildConfig(argv, env) {
  const isDryRun = argv.includes('--dry-run');
  const telegramSendsRequested = env.ENABLE_TELEGRAM_SENDS === 'true';

  return {
    isDryRun,
    cutoffReceipt: env.LOYVERSE_REPLAY_CUTOFF_RECEIPT || '',
    telegramSendsEnabled: telegramSendsRequested && !isDryRun,
  };
}

function main(argv, env) {
  const config = buildConfig(argv, env);

  console.log(`Chronological replay starting (dry-run: ${config.isDryRun})`);
  console.log(`Cutoff receipt: ${config.cutoffReceipt || '(none)'}`);
  console.log(`Telegram sends enabled: ${config.telegramSendsEnabled}`);
  console.log('Chronological replay completed.');
}

if (require.main === module) {
  main(process.argv.slice(2), process.env);
}

module.exports = { buildConfig, main };
