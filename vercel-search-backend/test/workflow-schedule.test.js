'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workflowPath = path.resolve(__dirname, '../../.github/workflows/validate-vercel-search.yml');
const workflow = fs.readFileSync(workflowPath, 'utf8');

test('Vercel search monitor runs on staggered 15-minute marks', () => {
  assert.match(workflow, /cron:\s*['"]5,20,35,50 \* \* \* \*['"]/);
  assert.doesNotMatch(workflow, /cron:\s*['"]\*\/15 \* \* \* \*['"]/);
});
