const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {Document, element: el, fire, BrowserFormData} = require('./dom-harness.cjs');

function storage() {
  const values = new Map();
  return {values, getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key)};
}

function capture({local = storage(), session = storage(), user = '1', bound = false,
  values = {}, confirm = () => true, savedToken = null, withApp = false} = {}) {
  const document = new Document();
  document.createElement = tag => Object.assign(el(tag), {remove() {
    const siblings = this.parentElement.children;
    siblings.splice(siblings.indexOf(this), 1);
  }});
  const window = el('window');
  Object.assign(window, {localStorage: local, sessionStorage: session, confirm,
    crypto: {randomUUID: () => require('node:crypto').randomUUID()}});
  const fields = Object.fromEntries(['title', 'meeting_date', 'raw_text'].map(name =>
    [name, el(name === 'raw_text' ? 'textarea' : 'input', {name,
      value: values[name] ?? (name === 'meeting_date' ? '2026-09-06' : '')})]));
  const token = el('input', {type: 'hidden', name: 'capture_draft_token'});
  const status = el('p', {'data-capture-status': '', role: 'status'});
  const clear = el('button', {type: 'button', 'data-capture-clear': ''});
  const parse = el('button', {type: 'submit', name: 'intent', value: 'parse'});
  const save = el('button', {type: 'submit', name: 'intent', value: 'save'});
  const form = el('form', {'data-capture-draft': '', 'data-user-id': user,
    'data-bound': String(bound), 'data-submit-once': ''}, ...Object.values(fields), token,
    el('input', {name: 'csrfmiddlewaretoken', value: 'never-store-this'}), status, clear, parse, save);
  if (savedToken === null) document.body.append(form);
  else document.body.append(el('span', {'data-capture-saved': '', 'data-user-id': user,
    'data-capture-token': savedToken}));
  const context = vm.createContext({document, window, console});
  if (withApp) vm.runInContext(fs.readFileSync(path.join(__dirname, '../../static/js/app.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../static/js/capture-draft.js'), 'utf8'), context);
  fire(document, 'DOMContentLoaded');
  return {document, window, form, fields, token, status, clear, parse, save, local, session,
    edit(name, value) { fields[name].value = value; fire(fields[name], 'input'); }};
}

test('unsubmitted title, date and raw text survive leaving and reloading the capture page', () => {
  const ui = capture();
  ui.edit('title', '发布周会');
  ui.edit('meeting_date', '2026-09-07');
  ui.edit('raw_text', '小李负责联调\n保留逐行记录');
  assert.match(ui.status.textContent, /已暂存到此浏览器/);
  const restored = capture(ui);
  assert.equal(restored.fields.title.value, '发布周会');
  assert.equal(restored.fields.meeting_date.value, '2026-09-07');
  assert.equal(restored.fields.raw_text.value, '小李负责联调\n保留逐行记录');
  assert.equal([...ui.local.values.values()].some(value => value.includes('never-store-this')), false);
});

test('a different signed-in account cannot restore another account\'s content', () => {
  const first = capture({user: '41'});
  first.edit('raw_text', '甲账号的会议');
  const other = capture({...first, user: '42'});
  assert.equal(other.fields.raw_text.value, '');
  other.edit('raw_text', '乙账号的会议');
  assert.equal(capture({...first, user: '41'}).fields.raw_text.value, '甲账号的会议');
  assert.equal(capture({...first, user: '42'}).fields.raw_text.value, '乙账号的会议');
});

test('server validation errors keep the submitted input and refresh the recoverable draft', () => {
  const first = capture();
  first.edit('raw_text', '旧暂存');
  const invalid = capture({...first, bound: true, values: {title: '', raw_text: '服务器返回的本次提交'}});
  assert.equal(invalid.fields.raw_text.value, '服务器返回的本次提交');
  assert.equal(capture(first).fields.raw_text.value, '服务器返回的本次提交');
});

for (const intent of ['parse', 'save']) test(`a ${intent} submission retains input until the server confirms success`, () => {
  const ui = capture({withApp: true});
  ui.edit('title', '需要保留的会议');
  ui.edit('raw_text', '请求失败也不能丢');
  const submit = fire(ui.form, 'submit', {submitter: ui[intent]});
  assert.equal(submit.defaultPrevented, false);
  const payload = new BrowserFormData(ui.form);
  assert.equal(payload.get('intent'), intent);
  assert.match(payload.get('capture_draft_token'), /^[A-Za-z0-9_-]{1,100}$/);
  assert.equal(capture(ui).fields.raw_text.value, '请求失败也不能丢');
  capture({...ui, savedToken: ui.token.value});
  assert.equal(capture(ui).fields.raw_text.value, '');
});

test('a success page cannot discard a newer edit or a draft submitted from another tab', () => {
  const ui = capture();
  ui.edit('raw_text', '已提交的版本');
  fire(ui.form, 'submit');
  const submitted = ui.token.value;
  capture({...ui, session: storage(), savedToken: submitted});
  assert.equal(capture(ui).fields.raw_text.value, '已提交的版本');
  const otherTab = capture({...ui, session: storage()});
  otherTab.edit('raw_text', '另一个标签的新内容');
  capture({...ui, savedToken: submitted});
  assert.equal(capture(ui).fields.raw_text.value, '另一个标签的新内容');
});

test('unavailable browser storage warns and prevents accidental loss on navigation', () => {
  const local = {getItem() { throw Error('blocked'); }, setItem() { throw Error('blocked'); }};
  const ui = capture({local});
  assert.match(ui.status.textContent, /无法暂存/);
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, false);
  ui.edit('raw_text', '尚未保存的内容');
  assert.match(ui.status.textContent, /尚未保存/);
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, true);
  assert.equal(fire(ui.form, 'submit').defaultPrevented, false);
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, false);
});

test('quota exhaustion keeps current input visible and announces that it is not saved', () => {
  const local = storage();
  const ui = capture({local});
  ui.edit('raw_text', '之前已保存');
  local.setItem = () => { throw Error('quota'); };
  ui.edit('raw_text', '仍然保留当前编辑');
  assert.equal(ui.fields.raw_text.value, '仍然保留当前编辑');
  assert.match(ui.status.textContent, /尚未保存/);
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, true);
});

test('clearing requires confirmation and resets only the signed-in account\'s capture draft', () => {
  let confirmed = false;
  const ui = capture({confirm: () => confirmed});
  ui.edit('title', '可清除');
  ui.edit('meeting_date', '2026-09-08');
  ui.edit('raw_text', '会议原文');
  const other = capture({...ui, user: '2'});
  other.edit('raw_text', '别人的内容');
  fire(ui.clear, 'click');
  assert.equal(ui.fields.raw_text.value, '会议原文');
  assert.equal(capture(ui).fields.raw_text.value, '会议原文');
  confirmed = true;
  fire(ui.clear, 'click');
  assert.equal(ui.fields.title.value, '');
  assert.equal(ui.fields.raw_text.value, '');
  assert.equal(ui.fields.meeting_date.value, '2026-09-06');
  assert.equal(capture(ui).fields.raw_text.value, '');
  assert.equal(capture({...ui, user: '2'}).fields.raw_text.value, '别人的内容');
  assert.match(ui.status.textContent, /已清空/);
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, false);
});

test('malformed stored data never fills controls with invalid values', () => {
  const ui = capture();
  ui.edit('raw_text', '原文');
  const key = [...ui.local.values.keys()][0];
  for (const damaged of ['not json', '{}', '{"title":null,"meeting_date":3,"raw_text":{}}']) {
    ui.local.setItem(key, damaged);
    const next = capture(ui);
    assert.equal(next.fields.title.value, '');
    assert.equal(next.fields.raw_text.value, '');
    assert.equal(next.fields.meeting_date.value, '2026-09-06');
    next.edit('raw_text', '新输入仍可保存');
    assert.equal(capture(ui).fields.raw_text.value, '新输入仍可保存');
  }
});

test('returned invalid input remains protected when browser storage is blocked before editing', () => {
  const local = {getItem() { throw Error('blocked'); }, setItem() { throw Error('blocked'); }};
  const ui = capture({local, bound: true, values: {title: '', raw_text: '服务端校验未通过的原文'}});
  assert.equal(ui.fields.raw_text.value, '服务端校验未通过的原文');
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, true);
});

test('blocked session storage never blocks meeting submission or discards a saved browser draft', () => {
  const session = {getItem() { throw Error('blocked'); }, setItem() { throw Error('blocked'); }};
  const ui = capture({session});
  ui.edit('raw_text', '保留本地备份');
  assert.equal(fire(ui.form, 'submit').defaultPrevented, false);
  capture({...ui, savedToken: ui.token.value});
  assert.equal(capture(ui).fields.raw_text.value, '保留本地备份');
});

test('back after confirmed save starts a fresh capture and restores both submit buttons', () => {
  const ui = capture({withApp: true});
  ui.edit('title', '已经保存的会议');
  ui.edit('raw_text', '已提交原文');
  fire(ui.form, 'submit', {submitter: ui.parse});
  capture({...ui, savedToken: ui.token.value});
  fire(ui.window, 'pageshow', {persisted: true});
  assert.equal(ui.fields.title.value, '');
  assert.equal(ui.fields.raw_text.value, '');
  assert.equal(ui.parse.disabled, false);
  assert.equal(ui.save.disabled, false);
  ui.edit('title', '新的会议');
  ui.edit('raw_text', '新的原文');
  assert.equal(fire(ui.form, 'submit', {submitter: ui.save}).defaultPrevented, false);
  assert.deepEqual(new BrowserFormData(ui.form).getAll('intent'), ['save']);
  assert.equal(capture(ui).fields.raw_text.value, '新的原文');
});

test('back after save can recover a newer draft without resurrecting the submitted record', () => {
  const ui = capture({withApp: true});
  ui.edit('raw_text', '已经提交');
  fire(ui.form, 'submit', {submitter: ui.save});
  const token = ui.token.value;
  const other = capture({...ui, session: storage()});
  other.edit('raw_text', '其他页面的新记录');
  capture({...ui, savedToken: token});
  fire(ui.window, 'pageshow', {persisted: true});
  assert.equal(ui.fields.raw_text.value, '其他页面的新记录');
  assert.equal(capture(ui).fields.raw_text.value, '其他页面的新记录');
});

test('back after another page cleared local storage marks the cached input unsaved', () => {
  const ui = capture();
  ui.edit('raw_text', '缓存页面中的原文');
  ui.local.values.clear();
  fire(ui.window, 'pageshow', {persisted: true});
  assert.match(ui.status.textContent, /尚未暂存/);
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, true);
  fire(ui.fields.raw_text, 'change');
  assert.equal(capture(ui).fields.raw_text.value, '缓存页面中的原文');
});
