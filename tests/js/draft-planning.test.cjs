const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {Document, element: el, fire, BrowserFormData} = require('./dom-harness.cjs');

function setup() {
  const document = new Document();
  const phase = el('select', {'data-plan-field': 'phase_id', value: '1'});
  const day = el('input', {'data-plan-field': 'planned_for', value: '2026-09-10'});
  const due = el('input', {name: 'task_0_due_date', value: '2026-09-11'});
  const target = el('select', {value: '2'});
  const action = el('select', {'data-task-action': '', value: 'update'});
  const warning = el('p', {'data-plan-warning': ''});
  const script = el('script', {type: 'application/json'});
  const phaseEdited = el('input', {type: 'hidden', name: 'task_0_phase_edited', 'data-plan-edited-for':'phase_id',value:''});
  const dayEdited = el('input', {type: 'hidden', name: 'task_0_planned_for_edited', 'data-plan-edited-for':'planned_for',value:''});
  script.textContent = JSON.stringify({'2': {phase_id: '3', planned_for: '2026-09-12'}});
  const plan = el('details', {'data-planning-review': ''}, phase, day, warning, script, phaseEdited, dayEdited);
  const row = el('details', {'data-review-row': ''}, plan, due, action,
    el('div', {'data-existing-field': ''}, target));
  document.body.append(row);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js/review-draft.js'), 'utf8'), {document});
  fire(document, 'DOMContentLoaded');
  return {phase, day, due, target, warning, row};
}

test('switch target fills untouched planning fields, while preserving explicit edits', () => {
  const ui = setup();
  ui.phase.value = '5';
  fire(ui.phase, 'change');
  fire(ui.target, 'change');
  assert.equal(ui.phase.value, '5');
  assert.equal(ui.day.value, '2026-09-12');
});

test('late planned date is a nonblocking warning and never changes the deadline', () => {
  const ui = setup();
  assert.equal(ui.warning.hidden, true);
  ui.day.value = '2026-09-15';
  fire(ui.day, 'input');
  assert.equal(ui.warning.hidden, false);
  assert.equal(ui.day.disabled, false);
  assert.equal(ui.due.value, '2026-09-11');
  ui.day.value = '';
  fire(ui.day, 'input');
  assert.equal(ui.warning.hidden, true);
});

test('autofill then clear submits explicit markers through ordinary FormData', () => {
  const ui=setup();
  fire(ui.target,'change');
  assert.equal(ui.phase.value,'3'); assert.equal(ui.day.value,'2026-09-12');
  assert.equal(new BrowserFormData(ui.row).get('task_0_phase_edited'),'false');
  ui.phase.value=''; fire(ui.phase,'change');
  ui.day.value=''; fire(ui.day,'input');
  const payload=new BrowserFormData(ui.row);
  assert.equal(payload.get('task_0_phase_edited'),'true');
  assert.equal(payload.get('task_0_planned_for_edited'),'true');
  fire(ui.target,'change');
  assert.equal(ui.phase.value,''); assert.equal(ui.day.value,'');
});
