'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { parseArgs } = require('./recover-chronological');

test('parseArgs enables dry-run mode', () => {
  assert.deepEqual(parseArgs(['--dry-run']), { dryRun: true });
});

test('parseArgs rejects unknown arguments', () => {
  assert.throws(() => parseArgs(['--unexpected']), /Unrecognized argument/);
});
