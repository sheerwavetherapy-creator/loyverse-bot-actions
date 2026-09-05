'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { parseArgs } = require('./recover-chronological');

test('parseArgs sets dryRun to true when --dry-run is present', () => {
  const args = parseArgs(['--dry-run']);
  assert.equal(args.dryRun, true);
});

test('parseArgs defaults dryRun to false when no arguments are given', () => {
  const args = parseArgs([]);
  assert.equal(args.dryRun, false);
});

test('parseArgs throws on unrecognized arguments', () => {
  assert.throws(() => parseArgs(['--bogus']), /Unrecognized argument\(s\): --bogus/);
});
