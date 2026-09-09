const test = require('node:test');
const assert = require('node:assert/strict');
const {Workspace, SaveQueue, once, requestJSON, scheduleAfterDeadline} = require('../../static/js/meeting-workspace.js');

test('meeting date reminder is nonblocking and excludes completed or unarranged tasks', () => {
  const item = {status: 'in_progress', planned_for: '2026-09-12', due_date: '2026-09-11'};
  assert.equal(scheduleAfterDeadline(item), true);
  assert.equal(scheduleAfterDeadline({...item, status: 'done'}), false);
  assert.equal(scheduleAfterDeadline({...item, planned_for: ''}), false);
  assert.equal(scheduleAfterDeadline({...item, planned_for: '2026-09-11'}), false);
  assert.equal(item.due_date, '2026-09-11');
});

test('selecting a linked task twice keeps stable ID and reporting-person switches preserve inputs', () => {
  const w = new Workspace({items: []}, () => 'stable-id');
  w.person = 2;
  const item = w.addTask({id: 5, title: '发货接口', assignee_id: 7, project_id: 1, baseline: 'signed'});
  item.completed_work = '已联调';
  w.person = 3;
  assert.equal(w.addTask({id: 5}), item);
  assert.equal(item.id, 'stable-id');
  assert.equal(item.completed_work, '已联调');
  assert.equal(item.person_id, 2);
});

test('autosave is serial and saves newer edits before preview navigation', async () => {
  let state = {title: '一'}; let release; const calls = []; const visited = [];
  const queue = new SaveQueue({version: 0, getState: () => state,
    apply: (value) => { state = value; }, save: async (data) => {
      calls.push(data); if (calls.length === 1) await new Promise((resolve) => { release = resolve; });
      return {version: data.version + 1, state: {...data.state, title: data.state.title + '规范化'}};
    }});
  queue.mark(); const pending = queue.flush();
  state.title = '二'; queue.mark();
  const navigation = queue.navigate('/preview', (url) => visited.push(url));
  assert.equal(calls.length, 1); release(); await pending; await navigation;
  assert.deepEqual(calls.map((x) => [x.version, x.state.title]), [[0, '一'], [1, '二']]);
  assert.equal(state.title, '二规范化'); assert.deepEqual(visited, ['/preview']); assert.equal(queue.dirty, false);
});

test('failure keeps draft dirty and prevents leaving; retry succeeds', async () => {
  let fail = true; let navigated = false;
  const queue = new SaveQueue({version: 3, getState: () => ({title: '保留'}), apply: () => {},
    save: async () => { if (fail) throw new Error('离线'); return {version: 4, state: {title: '保留'}}; }});
  queue.mark(); assert.equal(await queue.navigate('/', () => { navigated = true; }), false);
  assert.equal(queue.dirty, true); assert.equal(navigated, false);
  fail = false; assert.equal(await queue.flush(), true); assert.equal(queue.version, 4);
});

test('version conflicts block automatic overwrite and retain local content', async () => {
  let calls = 0; const state = {title: '本地编辑'};
  const queue = new SaveQueue({version: 1, getState: () => state, apply: () => assert.fail('must retain'),
    save: async () => { calls++; throw Object.assign(new Error('版本冲突'), {conflict: true}); }});
  queue.mark(); await queue.flush(); queue.mark(); await queue.flush();
  assert.equal(calls, 1); assert.equal(queue.conflict, true); assert.equal(queue.dirty, true);
  assert.equal(state.title, '本地编辑');
});

test('confirmation handler rejects repeated submits', () => {
  const accept = once(); assert.equal(accept(), true); assert.equal(accept(), false);
});

test('save request timeout covers response body, aborts and keeps local edits', async () => {
  const original = global.fetch; let signal;
  global.fetch = async (_url, options) => { signal = options.signal; return {
    ok: true, headers: {get: () => 'application/json'}, json: () => new Promise(() => {}),
  }; };
  try {
    const queue = new SaveQueue({version: 0, getState: () => ({title: '慢连接下的输入'}), apply: () => assert.fail(),
      save: (body) => requestJSON('/save', body, 'csrf', 10)});
    queue.mark(); assert.equal(await queue.flush(), false);
    assert.equal(queue.dirty, true); assert.equal(signal.aborted, true);
  } finally { global.fetch = original; }
});

test('native confirmation form disables repeated submits and restores original state after BFCache', () => {
  const vm = require('node:vm'); const fs = require('node:fs');
  const {Document, element, fire} = require('./dom-harness.cjs');
  const doc = new Document(); const submit = element('button', {type: 'submit'});
  const form = element('form', {'data-meeting-confirm': ''}, submit); doc.body.append(form);
  const win = new Document();
  const context = vm.createContext({document: doc, window: win, console});
  vm.runInContext(fs.readFileSync(require.resolve('../../static/js/meeting-workspace.js'), 'utf8'), context);
  fire(doc, 'DOMContentLoaded');
  assert.equal(fire(form, 'submit').defaultPrevented, false); assert.equal(submit.disabled, true);
  assert.equal(fire(form, 'submit').defaultPrevented, true);
  fire(win, 'pageshow', {persisted: true});
  assert.equal(submit.disabled, false);
  assert.equal(fire(form, 'submit').defaultPrevented, false);
});

test('BFCache restores creation form and preserves server-disabled confirmation controls', () => {
  const vm = require('node:vm'); const fs = require('node:fs');
  const {Document, element, fire} = require('./dom-harness.cjs');
  for (const [attribute, disabled] of [['data-meeting-create', false], ['data-meeting-confirm', true]]) {
    const doc = new Document(); const win = new Document();
    const submit = element('button', {type: 'submit'}); submit.disabled = disabled; submit.textContent = '原始标签';
    const form = element('form', {[attribute]: ''}, submit); doc.body.append(form);
    const context = vm.createContext({document: doc, window: win, console});
    vm.runInContext(fs.readFileSync(require.resolve('../../static/js/meeting-workspace.js'), 'utf8'), context);
    fire(doc, 'DOMContentLoaded'); fire(form, 'submit'); fire(win, 'pageshow', {persisted: true});
    assert.equal(submit.disabled, disabled); assert.equal(submit.textContent, '原始标签');
    if (!disabled) assert.equal(fire(form, 'submit').defaultPrevented, false);
  }
});

test('no-change restores immutable original baseline even if the latest task catalog has moved on', () => {
  const w = new Workspace({items: []}, () => 'stable');
  const task = {id: 5, title: '接口联调', status: 'in_progress', progress: 30, due_date: '2026-09-08'};
  const item = w.addTask(task); task.status = 'done'; task.progress = 100;
  w.update(item.id, 'progress', 70); w.noChange(item.id);
  assert.equal(item.progress, 30); assert.equal(item.status, 'in_progress'); assert.equal(item.recorded, true);
  delete item.baseline_values;
  assert.equal(w.noChange(item.id), false); assert.equal(item.progress, 30);
  item.baseline_values = {};
  assert.equal(w.noChange(item.id), false); assert.equal(item.progress, 30);
});

test('malformed or stale save acknowledgements cannot clear unsaved edits', async () => {
  for (const response of [{state: {}}, {version: 2, state: {}}, {version: 3}, {version: 3, state: {}, confirmed: true}]) {
    const queue = new SaveQueue({version: 2, getState: () => ({title: '保留'}), apply: () => assert.fail(), save: async () => response});
    queue.mark(); assert.equal(await queue.flush(), false); assert.equal(queue.dirty, true);
  }
});

test('writing task progress records the conversation, while selection and reporter changes do not; explicit exclusion wins', () => {
  const w = new Workspace({items: []}, () => 'stable');
  const item = w.addTask({id: 5, title: '接口联调', status: 'in_progress', progress: 30});
  assert.equal(item.recorded, false);
  w.update(item.id, 'person_id', 3); assert.equal(item.recorded, false);
  w.update(item.id, 'completed_work', '已完成联调'); assert.equal(item.recorded, true);
  w.update(item.id, 'recorded', false); assert.equal(item.recorded, false);
  assert.equal(item.completed_work, '已完成联调');
});

test('changing a new task project clears the old phase before autosave', async () => {
  const w = new Workspace({items: []}, () => 'new-task');
  const item = w.add('new_task', 1);
  w.update(item.id, 'phase_id', 12);
  w.update(item.id, 'title', '联调事项');
  w.update(item.id, 'project_id', 2);
  let saved;
  const queue = new SaveQueue({version: 0, getState: () => w.state, apply: () => {},
    save: async (data) => { saved = data.state; return {version: 1, state: data.state}; }});
  queue.mark(); assert.equal(await queue.flush(), true);
  assert.equal(saved.items[0].phase_id, null);
  assert.equal(saved.items[0].project_id, 2);
  assert.equal(saved.items[0].title, '联调事项');
});

test('meeting counts distinguish asked tasks from pending tasks and newly recorded notes', () => {
  const w = new Workspace({items: [
    {id: 'a', kind: 'task', person_id: 1, recorded: true},
    {id: 'b', kind: 'task', person_id: 1, recorded: false},
    {id: 'c', kind: 'task', person_id: 2, recorded: false},
    {id: 'd', kind: 'risk', person_id: 1, recorded: true},
  ]});
  assert.deepEqual(w.counts(), {total: 3, asked: 1, pending: 2, added: 1});
  assert.deepEqual(w.counts(2), {total: 1, asked: 0, pending: 1, added: 0});
  assert.deepEqual(w.counts(3), {total: 0, asked: 0, pending: 0, added: 0});
});

test('pending-only mode retains the active task while typing and never discards hidden records', () => {
  const w = new Workspace({items: [
    {id: 'a', kind: 'task', person_id: 1, recorded: false},
    {id: 'b', kind: 'task', person_id: 1, recorded: true},
    {id: 'c', kind: 'note', person_id: 1, recorded: true, content: '保留纪要'},
  ]});
  w.pendingOnly = true; w.focusRecord('a');
  w.update('a', 'completed_work', '正在输入的完成情况');
  assert.deepEqual(w.records().map(item => item.id), ['a']);
  assert.equal(w.counts().pending, 0);
  w.activeId = null;
  assert.deepEqual(w.records(), []);
  w.pendingOnly = false;
  assert.equal(w.records().length, 3);
  assert.equal(w.state.items[0].completed_work, '正在输入的完成情况');
  assert.equal(w.state.items[2].content, '保留纪要');
});

test('switching reporters only changes the visible scope and leaves all draft input intact', () => {
  const w = new Workspace({items: [
    {id: 'a', kind: 'task', person_id: 1, recorded: false, next_step: '周五前联调'},
    {id: 'b', kind: 'task', person_id: 2, recorded: false},
  ]});
  w.focusRecord('a'); w.selectPerson(2);
  assert.deepEqual(w.records().map(item => item.id), ['b']);
  assert.equal(w.activeId, undefined);
  w.selectPerson(1);
  assert.equal(w.records()[0].next_step, '周五前联调');
});

test('focusing a new note exits pending-only mode so its editor is not hidden', () => {
  const w = new Workspace({items: []}, () => 'new-note');
  w.pendingOnly = true; w.person = 2;
  const item = w.add('note');
  assert.equal(w.focusRecord(item.id), true);
  assert.equal(w.pendingOnly, false);
  assert.equal(w.records()[0].id, item.id);
  assert.equal(w.focusRecord('missing'), false);
});

test('next pending task follows meeting order within the selected person and does not mark it asked', () => {
  const w = new Workspace({items: [
    {id: 'a', kind: 'task', person_id: 1, recorded: false},
    {id: 'b', kind: 'task', person_id: 2, recorded: false},
    {id: 'c', kind: 'task', person_id: 1, recorded: false},
  ]});
  w.selectPerson(1); w.focusRecord('a');
  assert.equal(w.nextPending(), 'c');
  assert.equal(w.state.items[0].recorded, false);
  w.focusRecord('c');
  assert.equal(w.nextPending(), 'a');
  w.update('a', 'recorded', true);
  assert.equal(w.nextPending(), null);
});
