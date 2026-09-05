const assert = require('node:assert/strict');
const test = require('node:test');
const {Document, element: el, loadApp, fire, BrowserFormData} = require('./dom-harness.cjs');

function review({action = 'ignore', project = '', target = '7', needsAttention = true, date,
  recommended = 'create', includeProject = true, task = true} = {}) {
  const document = new Document();
  const select = el('select', {name: task ? 'task_0_action' : 'risks_0_action', value: action, ...(task ? {'data-task-action': ''} : {})});
  const projectSelect = el('select', {name: 'task_0_project', value: project, 'data-review-project': ''});
  const existing = el('select', {name: 'task_0_existing', value: target});
  const dateInput = el('input', {name: 'task_0_due_date', value: date || '', type: 'text',
    'data-review-date': '', 'data-date-needs-correction': 'true'});
  const row = el('details', {class: 'review-item', 'data-review-row': '',
    'data-needs-attention': String(needsAttention), 'data-recommended-action': recommended,
    'data-recommended-existing': '7'}, select, el('span', {'data-action-label': ''}));
  if (includeProject) row.append(projectSelect);
  if (task) row.append(el('div', {'data-existing-field': ''}, existing));
  if (date !== undefined) row.append(dateInput);
  const summary = el('div', {'data-review-summary': ''});
  const filter = el('button', {'data-review-filter': 'attention'});
  const batch = el('button', {'data-batch-action': 'recommended'});
  const ignore = el('button', {'data-batch-action': 'ignore-attention'});
  const form = el('form', {'data-review-form': ''}, row, summary, filter, batch, ignore,
    el('button', {'data-review-filter': 'all'}), el('button', {'data-review-submit': ''}), el('p', {'data-review-empty': ''}));
  document.body.append(form);
  loadApp(document);
  fire(filter, 'click');
  const choose = value => { select.value = value; select.focus(); fire(select, 'change'); };
  const attention = value => assert.match(summary.textContent, new RegExp(`需确认 ${value} 项`));
  return {document, row, select, projectSelect, existing, dateInput, batch, ignore, choose, attention};
}

test('attention filter retains incomplete create without losing focused control', () => {
  const ui = review();
  ui.choose('create');
  ui.attention(1);
  assert.equal(ui.row.hidden, false);
  assert.equal(ui.document.activeElement, ui.select);
  ui.projectSelect.value = '1';
  fire(ui.projectSelect, 'change');
  ui.attention(0);
  assert.equal(ui.row.hidden, true);
});

test('update requires a selected existing task; ignore explicitly resolves it', () => {
  const ui = review({project: '1', target: ''});
  ui.choose('update');
  ui.attention(1);
  assert.equal(ui.row.hidden, false);
  ui.existing.value = '7';
  fire(ui.existing, 'change');
  ui.attention(0);
  ui.existing.value = '';
  fire(ui.existing, 'change');
  ui.attention(1);
  ui.choose('ignore');
  ui.attention(0);
});

test('bad date stays in attention until a real ISO calendar date is entered', () => {
  const ui = review({project: '1', date: '下下个月某天'});
  ui.choose('create');
  for (const value of ['', '2026-02-30', '2026/09/05', '2026-13-01']) {
    ui.dateInput.value = value;
    fire(ui.dateInput, 'input');
    ui.attention(1);
    assert.equal(ui.row.hidden, false);
  }
  ui.dateInput.value = '2026-09-05';
  fire(ui.dateInput, 'input');
  ui.attention(0);
});

test('accept recommendation keeps invalid project/date; ignore-attention resolves', () => {
  const ui = review({date: '待定'});
  fire(ui.batch, 'click');
  ui.attention(1);
  assert.equal(ui.row.hidden, false);
  ui.projectSelect.value = '1';
  fire(ui.projectSelect, 'change');
  ui.attention(1);
  fire(ui.ignore, 'click');
  ui.attention(0);
});

test('validity is scoped to controls present on risk/milestone rows', () => {
  const ui = review({action: 'create', needsAttention: false, includeProject: false, task: false});
  ui.attention(0);
  fire(ui.batch, 'click');
  ui.attention(0);
});

test('supplemental rows validate a project when that control is present', () => {
  const ui = review({action: 'ignore', needsAttention: false, task: false});
  ui.choose('create');
  ui.attention(1);
  ui.projectSelect.value = '1';
  fire(ui.projectSelect, 'change');
  ui.attention(0);
});

test('recommended update cannot clear an empty existing target', () => {
  const ui = review({project: '1', recommended: 'update'});
  ui.row.setAttribute('data-recommended-existing', '');
  fire(ui.batch, 'click');
  ui.attention(1);
  assert.equal(ui.row.hidden, false);
});

for (const intent of ['parse', 'save']) test(`native FormData retains clicked ${intent} intent after submit-once`, () => {
  const document = new Document();
  const parse = el('button', {type: 'submit', name: 'intent', value: 'parse'});
  const save = el('button', {type: 'submit', name: 'intent', value: 'save'});
  const form = el('form', {'data-submit-once': ''}, parse, save, el('input', {name: 'title', value: '周会'}));
  document.body.append(form);
  loadApp(document);
  const event = fire(form, 'submit', {submitter: intent === 'parse' ? parse : save});
  assert.equal(event.defaultPrevented, false);
  assert.deepEqual([...new BrowserFormData(form)], [['title', '周会'], ['intent', intent]]);
  assert.equal(fire(form, 'submit', {submitter: parse}).defaultPrevented, true);
});

test('copy failure is announced through the status region', async () => {
  const document = new Document();
  const button = el('button', {'data-copy-target': 'text'});
  const status = el('div', {'data-copy-status': ''});
  document.body.append(button, status, el('textarea', {id: 'text', value: '跟进内容'}));
  loadApp(document, {navigator: {clipboard: {writeText: async () => { throw Error('denied'); }}}});
  fire(button, 'click');
  await new Promise(resolve => setImmediate(resolve));
  assert.match(status.textContent, /复制失败/);
});

test('auto parse consumes URL intent and submits only once, even with duplicate clicks', async () => {
  const document = new Document();
  const button = el('button', {'data-parse-button': ''}, el('span', {'data-button-label': ''}));
  const form = el('form', {'data-parse-form': '', action: '/meetings/1/parse/'}, button);
  const progress = el('div', {'data-parse-progress': ''}, ...['title', 'detail'].map(name => el('span', {[`data-progress-${name}`]: ''})), el('span', {'data-elapsed': ''}));
  document.body.append(form, progress, el('p', {'data-auto-parse': ''}));
  let calls = 0;
  const app = loadApp(document, {fetch: async () => { calls += 1; return new Promise(() => {}); }});
  form.requestSubmit();
  assert.equal(calls, 1);
  assert.equal(new URL(app.window.location.href).searchParams.has('auto_parse'), false);
});

test('shortcuts do not hijack fields, composition, editable content or modifiers', () => {
  const document = new Document();
  const search = el('input', {'data-search-input': ''});
  const link = el('a', {'data-shortcut-new-meeting': '', href: '/meetings/new/'});
  const fields = ['input', 'textarea', 'select'].map(tag => el(tag));
  const editable = el('span'); editable.isContentEditable = true;
  document.body.append(search, link, ...fields, editable);
  const app = loadApp(document);
  for (const target of [...fields, editable]) assert.equal(fire(target, 'keydown', {key: 'n'}).defaultPrevented, false);
  for (const key of ['isComposing', 'ctrlKey', 'metaKey', 'altKey', 'shiftKey']) {
    assert.equal(fire(document.body, 'keydown', {key: 'n', [key]: true}).defaultPrevented, false);
  }
  assert.notEqual(app.window.location.href, '/meetings/new/');
  fire(document.body, 'keydown', {key: '/'});
  assert.equal(document.activeElement, search);
  fire(document.body, 'keydown', {key: 'n'});
  assert.equal(app.window.location.href, '/meetings/new/');
});
