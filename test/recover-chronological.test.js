'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { buildConfig } = require('../scripts/recover-chronological');

test('dry-run always disables Telegram sends', () => {
  const config = buildConfig(['--dry-run'], { ENABLE_TELEGRAM_SENDS: 'true' });

  assert.equal(config.isDryRun, true);
  assert.equal(config.telegramSendsEnabled, false);
});

test('replay reads its optional receipt cutoff', () => {
  const config = buildConfig([], { LOYVERSE_REPLAY_CUTOFF_RECEIPT: '1-4167' });

  assert.equal(config.cutoffReceipt, '1-4167');
});
