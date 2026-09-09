const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {Document, element: el, fire} = require('./dom-harness.cjs');

function taskForm() {
  const document = new Document();
  const project = el('select', {name: 'project', value: '1'});
  const phase = el('select', {name: 'phase', value: '11'});
  phase.replaceChildren = (...options) => { phase.children = []; phase.append(...options); };
  const status = el('small', {'data-task-phase-status': ''});
  const form = el('form', {'data-task-form': ''}, project, phase, status);
  const data = el('script', {id: 'task-phase-options'});
  data.textContent = JSON.stringify([
    {id: 11, project_id: 1, name: '开发'}, {id: 12, project_id: 1, name: '验收'},
    {id: 21, project_id: 2, name: '<结算>'},
  ]);
  document.body.append(form, data);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js/task-form.js'), 'utf8'), {document});
  return {project, phase, status};
}

test('changing project offers only its phases and clears an invalid previous phase', () => {
  const ui = taskForm();
  assert.equal(ui.phase.value, '11');
  assert.deepEqual(ui.phase.children.map(option => option.value), ['', '11', '12']);
  ui.project.value = '2';
  fire(ui.project, 'change');
  assert.equal(ui.phase.value, '');
  assert.deepEqual(ui.phase.children.map(option => option.value), ['', '21']);
  assert.equal(ui.phase.children[1].textContent, '<结算>');
  assert.match(ui.status.textContent, /已清空/);
});

test('clearing project removes phase choices without keeping a hidden stale phase', () => {
  const ui = taskForm();
  ui.project.value = '';
  fire(ui.project, 'change');
  assert.equal(ui.phase.value, '');
  assert.deepEqual(ui.phase.children.map(option => option.value), ['']);
});
