'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { buildConfig } = require('../scripts/recover-chronological');

test('buildConfig defaults telegram sends to disabled when env var absent', () => {
  const config = buildConfig(['--dry-run'], {});
  assert.equal(config.isDryRun, true);
  assert.equal(config.telegramSendsRequested, false);
  assert.equal(config.telegramSendsEnabled, false);
  assert.equal(config.renderApiKey, '');
  assert.equal(config.serviceId, '');
  assert.equal(config.cutoffReceipt, '');
});

test('buildConfig forces telegram sends off during --dry-run even if requested', () => {
  const config = buildConfig(['--dry-run'], { ENABLE_TELEGRAM_SENDS: 'true' });
  assert.equal(config.isDryRun, true);
  assert.equal(config.telegramSendsRequested, true);
  assert.equal(config.telegramSendsEnabled, false);
});

test('buildConfig enables telegram sends outside dry-run when requested', () => {
  const config = buildConfig([], { ENABLE_TELEGRAM_SENDS: 'true' });
  assert.equal(config.isDryRun, false);
  assert.equal(config.telegramSendsEnabled, true);
});

test('buildConfig reads render/service/cutoff values from env', () => {
  const config = buildConfig([], {
    RENDER_API_KEY: 'key-123',
    SERVICE_ID: 'service-456',
    LOYVERSE_REPLAY_CUTOFF_RECEIPT: '1-4167',
  });
  assert.equal(config.renderApiKey, 'key-123');
  assert.equal(config.serviceId, 'service-456');
  assert.equal(config.cutoffReceipt, '1-4167');
});
