'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const pkg = require('../src/package.json');

test('Vercel search package explicitly pins CommonJS and Node 26', () => {
  assert.equal(pkg.private, true);
  assert.equal(pkg.type, 'commonjs');
  assert.equal(pkg.engines.node, '26.x');
  assert.equal(pkg.dependencies, undefined);
});
