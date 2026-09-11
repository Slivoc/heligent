/* eslint-disable @typescript-eslint/no-require-imports */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const source = fs.readFileSync(require('node:path').join(__dirname, '../app/maintenance-watchlist.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const context = { exports: {} };
vm.runInNewContext(compiled, context);
const { filterWatches, watchOptions, emptyWatchFilters, unknownValue } = context.exports;
const watches = [
  { id: 1, registration: 'GTEST', operator: 'North Air', type_code: 'EC45', last_confirmed_maintenance: '2026-09-07T00:00:00Z' },
  { id: 2, registration: 'G-OTHER', operator: 'North Air', type_code: 'A139', last_confirmed_maintenance: null },
  { id: 3, registration: 'N551HY', operator: 'Coastal Helicopters', type_code: 'EC45' },
  { id: 4, registration: 'GEMPTY', operator: null, type_code: null },
];
const matching = filters => Array.from(filterWatches(watches, { ...emptyWatchFilters, ...filters }), watch => watch.id);

test('registration search accepts mixed case, spaces and hyphens; search also finds operators and types', () => {
  assert.deepEqual(matching({ search: ' g-test ' }), [1]);
  assert.deepEqual(matching({ search: 'gother' }), [2]);
  assert.deepEqual(matching({ search: 'NORTH' }), [1, 2]);
  assert.deepEqual(matching({ search: 'ec45' }), [1, 3]);
});

test('operator, aircraft type, search and review filters combine', () => {
  assert.deepEqual(matching({ operator: 'North Air', type: 'EC45', review: 'reviewed', search: 'g' }), [1]);
  assert.deepEqual(matching({ operator: 'North Air', review: 'needs-benchmark' }), [2]);
  assert.deepEqual(matching({ operator: 'Coastal Helicopters', review: 'reviewed' }), []);
  assert.deepEqual(matching({}), [1, 2, 3, 4]);
});

test('unknown identities remain filterable and available options come from the full watchlist', () => {
  assert.deepEqual(matching({ operator: unknownValue, type: unknownValue }), [4]);
  assert.deepEqual(Array.from(watchOptions(watches, 'operator')), ['Coastal Helicopters', 'North Air']);
  assert.deepEqual(Array.from(watchOptions(watches, 'type_code')), ['A139', 'EC45']);
  assert.deepEqual(matching({ operator: 'Operator no longer on list' }), []);
});
