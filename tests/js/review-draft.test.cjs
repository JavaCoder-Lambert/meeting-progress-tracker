const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {Document, element: el, BrowserFormData} = require('./dom-harness.cjs');

// Extend the shared DOM adapter with browser capture/bubble ordering. The tests
// run the actual app scripts and observe events, outgoing forms and navigation.
class UIEvent {
  constructor(type, options = {}) {
    Object.assign(this, {type, bubbles: true, cancelable: true, defaultPrevented: false}, options);
  }
  preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
  stopPropagation() { this.stopped = true; }
  stopImmediatePropagation() { this.immediate = true; this.stopped = true; }
}
function events(node) {
  node.listeners = new Map();
  node.addEventListener = (type, callback, options) => {
    const entries = node.listeners.get(type) || [];
    entries.push({callback, capture: options === true || options?.capture === true});
    node.listeners.set(type, entries);
  };
  node.dispatchEvent = event => {
    event.target ||= node;
    const route = [];
    for (let current = node; current; current = current.parentElement) route.push(current);
    const invoke = (current, capture) => {
      for (const entry of current.listeners.get(event.type) || []) {
        if (entry.capture === capture) entry.callback(event);
        if (event.immediate) break;
      }
    };
    for (const current of [...route].reverse()) {
      invoke(current, true);
      if (event.stopped) return !event.defaultPrevented;
    }
    for (const current of event.bubbles ? route : [node]) {
      invoke(current, false);
      if (event.stopped) break;
    }
    return !event.defaultPrevented;
  };
  node.remove = () => { node.parentElement.children = node.parentElement.children.filter(item => item !== node); };
  node.children.forEach(events);
  return node;
}
const fire = (target, type, options = {}) => {
  const event = new UIEvent(type, options);
  target.dispatchEvent(event);
  return event;
};
const settle = async () => {
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
};
const response = (version = 1, overrides = {}) => ({
  ok: true, status: 200, headers: {get: () => 'application/json'},
  json: async () => ({ok: true, version, saved_at: '2026-09-06T08:00:00Z'}), ...overrides,
});
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};

function review(fetch, {pauseReplay = false, withPlanning = false} = {}) {
  const document = new Document();
  const title = el('input', {name: 'task_0_title', value: '跟进报价'});
  const version = el('input', {type: 'hidden', name: 'review_version', value: '0'});
  const baseline = el('input', {type: 'hidden', name: 'review_baseline', value: 'signed-baseline'});
  const existing = el('select', {name: 'task_0_existing', value: '9'});
  existing.disabled = true;
  const save = el('button', {type: 'submit', name: 'destination', value: 'stay',
    formaction: '/meetings/1/review/save/', formnovalidate: '', 'data-draft-save': ''});
  save.textContent = '暂存草稿';
  const confirm = el('button', {type: 'submit', 'data-review-submit': ''});
  const create = el('button', {type: 'submit', name: 'destination', value: 'project:task_0_project',
    formaction: '/meetings/1/review/save/', formnovalidate: '', 'data-draft-leave': ''});
  create.textContent = '新建项目';
  const status = el('p', {'data-draft-status': '', role: 'status'});
  status.textContent = '修改会自动暂存到服务器';
  const form = el('form', {'data-review-form': '', 'data-submit-once': '',
    'data-draft-save-url': '/meetings/1/review/save/', action: '/meetings/1/review/'},
    title, version, baseline, existing, el('input', {name: 'csrfmiddlewaretoken', value: 'csrf-token'}),
    el('div', {'data-review-summary': ''}), el('p', {'data-review-empty': ''}), save, confirm, create, status);
  const link = el('a', {href: '/projects/'});
  if (withPlanning) {
    const plans = el('script', {type:'application/json'}); plans.textContent='{}';
    form.append(el('details', {class:'review-item','data-review-row':''},
      el('select', {name:'task_0_action','data-task-action':'',value:'ignore'}),
      el('details', {'data-planning-review':''},
        el('input', {type:'hidden',name:'task_0_phase_edited','data-plan-edited-for':'phase_id',value:''}),
        el('input', {type:'hidden',name:'task_0_planned_for_edited','data-plan-edited-for':'planned_for',value:''}),
        el('select', {name:'task_0_phase','data-plan-field':'phase_id','data-plan-edited':'false',value:''}),
        el('input', {name:'task_0_planned_for','data-plan-field':'planned_for','data-plan-edited':'false',value:''}),
        el('p', {'data-plan-warning':''}), plans)));
  }
  document.body.append(form, link);
  events(document);
  const createElement = document.createElement.bind(document);
  document.createElement = tag => events(createElement(tag));
  const timers = new Map();
  let nextTimer = 0;
  const window = events(el('window'));
  window.location = {href: 'http://localhost/meetings/1/review/', assign(value) { this.href = String(value); }};
  window.setTimeout = (callback, delay) => {
    const id = ++nextTimer;
    timers.set(id, {callback, delay});
    if (delay === 0 && !pauseReplay) setImmediate(() => {
      if (timers.has(id)) { timers.delete(id); callback(); }
    });
    return id;
  };
  window.clearTimeout = id => timers.delete(id);
  const requests = [];
  const submissions = [];
  form.requestSubmit = submitter => {
    const event = fire(form, 'submit', {submitter});
    if (!event.defaultPrevented) submissions.push({submitter, fields: [...new BrowserFormData(form)]});
  };
  const context = vm.createContext({document, window, Event: UIEvent, FormData: BrowserFormData,
    URL, AbortController, console, fetch: (url, options) => {
      requests.push({url, options});
      return fetch ? fetch(url, options, requests.length) : Promise.resolve(response(requests.length));
    }});
  for (const name of ['app.js', 'review-draft.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../static/js', name), 'utf8'), context, {filename: name});
  }
  fire(document, 'DOMContentLoaded');
  const tick = delay => {
    for (const [id, timer] of [...timers]) {
      if (timer.delay === delay) { timers.delete(id); timer.callback(); }
    }
  };
  const edit = value => { title.value = value; fire(title, 'input'); };
  return {document, form, title, version, baseline, existing, save, confirm, create, status,
    link, window, requests, submissions, edit, tick, timers};
}

test('edits debounce into acknowledged server drafts with native form fields', async () => {
  const ui = review();
  ui.edit('第一次修改');
  ui.edit('最终修改');
  assert.equal(ui.requests.length, 0);
  assert.equal(ui.status.dataset.state, 'dirty');
  ui.tick(700);
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.status.dataset.state, 'saving');
  const {url, options} = ui.requests[0];
  assert.equal(url, '/meetings/1/review/save/');
  assert.equal(options.method, 'POST');
  assert.equal(options.headers['X-Requested-With'], 'XMLHttpRequest');
  assert.equal(options.body.get('task_0_title'), '最终修改');
  assert.equal(options.body.get('csrfmiddlewaretoken'), 'csrf-token');
  assert.equal(options.body.get('review_baseline'), 'signed-baseline');
  assert.equal(options.body.has('task_0_existing'), false);
  assert.equal(options.body.has('destination'), false);
  await settle();
  assert.equal(ui.version.value, '1');
  assert.equal(ui.status.dataset.state, 'saved');
  assert.match(ui.status.textContent, /已暂存/);
});

test('planning initialization leaves an untouched review clean and does not save on navigation', async () => {
  const ui=review(undefined,{withPlanning:true});
  assert.equal(new BrowserFormData(ui.form).get('task_0_phase_edited'),'false');
  assert.equal(new BrowserFormData(ui.form).get('task_0_planned_for_edited'),'false');
  assert.equal(fire(ui.window,'beforeunload').defaultPrevented,false);
  assert.equal(fire(ui.link,'click',{button:0}).defaultPrevented,false);
  ui.tick(700); await settle();
  assert.equal(ui.requests.length,0);
  assert.equal(ui.version.value,'0');
  assert.notEqual(ui.status.dataset.state,'dirty');
});

test('edits during a request are saved serially with the acknowledged revision', async () => {
  const first = deferred();
  const second = deferred();
  const ui = review((_url, _options, count) => count === 1 ? first.promise : second.promise);
  ui.edit('旧修改');
  ui.tick(700);
  ui.edit('较新修改');
  ui.tick(700);
  assert.equal(ui.requests.length, 1);
  assert.notEqual(ui.status.dataset.state, 'saved');
  first.resolve(response(4));
  await settle();
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].options.body.get('review_version'), '4');
  assert.equal(ui.requests[1].options.body.get('task_0_title'), '较新修改');
  assert.equal(ui.status.dataset.state, 'saving');
  second.resolve(response(5));
  await settle();
  assert.equal(ui.version.value, '5');
  assert.equal(ui.status.dataset.state, 'saved');
});

test('manual draft save acknowledges without native submission or disabled controls', async () => {
  const pending = deferred();
  const ui = review(() => pending.promise);
  ui.edit('尚不完整的任务');
  ui.form.requestSubmit(ui.save);
  ui.form.requestSubmit(ui.save);
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.submissions.length, 0);
  assert.equal(ui.confirm.disabled, false);
  assert.notEqual(ui.form.dataset.submitting, 'true');
  pending.resolve(response());
  await settle();
  assert.equal(ui.status.dataset.state, 'saved');
  assert.equal(ui.submissions.length, 0);
});

for (const target of ['confirm', 'create']) test(`${target} waits for the latest edit then performs one native submission`, async () => {
  const first = deferred();
  const second = deferred();
  const ui = review((_url, _options, count) => count === 1 ? first.promise : second.promise);
  ui.edit('第一版');
  ui.tick(700);
  ui.form.requestSubmit(ui[target]);
  ui.form.requestSubmit(ui[target]);
  ui.edit('离开前最后一版');
  assert.equal(ui.submissions.length, 0);
  assert.equal(ui.confirm.disabled, false);
  first.resolve(response(1));
  await settle();
  assert.equal(ui.submissions.length, 0);
  second.resolve(response(2));
  await settle();
  assert.equal(ui.submissions.length, 1);
  assert.equal(ui.submissions[0].submitter, ui[target]);
  assert.equal(ui.form.dataset.submitting, 'true');
  assert.equal(ui.confirm.disabled, true);
  assert.ok(ui.submissions[0].fields.some(([name, value]) => name === 'review_version' && value === '2'));
  if (target === 'create') assert.ok(ui.submissions[0].fields.some(([name, value]) =>
    name === 'destination' && value === 'project:task_0_project'));
  assert.equal(ui.requests.some(({options}) => options.body.has('destination')), false);
});

test('same-tab navigation flushes drafts while beforeunload protects only unsaved work', async () => {
  const pending = deferred();
  const ui = review(() => pending.promise);
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, false);
  ui.edit('离开时也要留下');
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, true);
  assert.equal(fire(ui.link, 'click', {button: 0}).defaultPrevented, true);
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.window.location.href, 'http://localhost/meetings/1/review/');
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, true);
  pending.resolve(response());
  await settle();
  assert.equal(ui.window.location.href, 'http://localhost/projects/');
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, false);
});

test('network failure retains the page and edits; explicit retry can save and then leave', async () => {
  const ui = review((_url, _options, count) => count === 1 ? Promise.reject(Error('offline')) : Promise.resolve(response()));
  ui.edit('网络中断也不能丢');
  fire(ui.link, 'click', {button: 0});
  await settle();
  assert.equal(ui.window.location.href, 'http://localhost/meetings/1/review/');
  assert.equal(ui.status.dataset.state, 'failed');
  assert.match(ui.status.textContent, /重试/);
  assert.equal(ui.title.value, '网络中断也不能丢');
  assert.equal(ui.save.disabled, false);
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, true);
  ui.tick(700);
  assert.equal(ui.requests.length, 1);
  ui.form.requestSubmit(ui.save);
  await settle();
  assert.equal(ui.status.dataset.state, 'saved');
  assert.equal(fire(ui.link, 'click', {button: 0}).defaultPrevented, false);
});

for (const stalledAt of ['headers', 'body']) test(`a ${stalledAt} timeout aborts and never replays a pending confirmation`, async () => {
  const late = deferred();
  const ui = review(() => stalledAt === 'headers' ? late.promise : Promise.resolve(response(1, {json: () => late.promise})));
  ui.edit('超时前的修改');
  ui.form.requestSubmit(ui.confirm);
  await settle();
  ui.tick(10000);
  await settle();
  assert.equal(ui.requests[0].options.signal.aborted, true);
  assert.equal(ui.status.dataset.state, 'failed');
  assert.match(ui.status.textContent, /超时/);
  assert.equal(ui.confirm.disabled, false);
  assert.equal(ui.submissions.length, 0);
  late.resolve(stalledAt === 'headers' ? response(1) : {ok: true, version: 1, saved_at: '2026-09-06T08:00:00Z'});
  await settle();
  assert.equal(ui.version.value, '0');
  assert.equal(ui.status.dataset.state, 'failed');
  assert.equal(ui.submissions.length, 0);
  assert.equal(ui.requests.length, 1);
});

for (const invalid of ['login', 'http-error', 'rejected', 'malformed-json', 'missing-version', 'old-version']) {
  test(`${invalid} response never claims saved or advances to native submission`, async () => {
    const variants = {
      login: response(1, {redirected: true, headers: {get: () => 'text/html'}, json: async () => { throw Error('HTML'); }}),
      'http-error': response(1, {ok: false, status: 500}),
      rejected: response(1, {json: async () => ({ok: false, message: '暂存被拒绝'})}),
      'malformed-json': response(1, {json: async () => { throw Error('invalid JSON'); }}),
      'missing-version': response(1, {json: async () => ({ok: true})}),
      'old-version': response(0),
    };
    const ui = review(() => Promise.resolve(variants[invalid]));
    ui.edit('保留这段修改');
    ui.form.requestSubmit(ui.confirm);
    await settle();
    assert.equal(ui.version.value, '0');
    assert.equal(ui.status.dataset.state, 'failed');
    assert.equal(ui.submissions.length, 0);
    assert.equal(ui.confirm.disabled, false);
    if (invalid === 'login') assert.match(ui.status.textContent, /登录/);
  });
}

test('a stale or confirmed draft halts autosave and protects the current edits from overwriting another tab', async () => {
  const ui = review(() => Promise.resolve(response(1, {ok: false, status: 409,
    json: async () => ({ok: false, message: '这份草稿已在另一页面修改或入库'})})));
  ui.edit('当前页面自己的修改');
  ui.tick(700);
  await settle();
  assert.equal(ui.version.value, '0');
  assert.equal(ui.status.dataset.state, 'failed');
  assert.match(ui.status.textContent, /另一页面/);
  assert.match(ui.status.textContent, /复制/);
  assert.match(ui.status.textContent, /最新/);
  ui.edit('冲突后继续补记');
  ui.tick(700);
  ui.form.requestSubmit(ui.save);
  ui.form.requestSubmit(ui.confirm);
  fire(ui.link, 'click', {button: 0});
  await settle();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.status.dataset.state, 'failed');
  assert.equal(ui.submissions.length, 0);
  assert.equal(ui.title.value, '冲突后继续补记');
  assert.equal(ui.window.location.href, 'http://localhost/meetings/1/review/');
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, true);
});

test('returning through browser history releases submit-once controls and its preserved destination', async () => {
  const ui = review();
  ui.edit('创建前修改');
  ui.form.requestSubmit(ui.create);
  await settle();
  assert.equal(ui.create.disabled, true);
  assert.equal(new BrowserFormData(ui.form).get('destination'), 'project:task_0_project');
  fire(ui.window, 'pageshow', {persisted: true});
  assert.equal(ui.create.disabled, false);
  assert.equal(ui.create.textContent, '新建项目');
  assert.notEqual(ui.form.dataset.submitting, 'true');
  assert.equal(new BrowserFormData(ui.form).has('destination'), false);
  ui.edit('返回后修改');
  ui.form.requestSubmit(ui.save);
  await settle();
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].options.body.get('review_version'), '1');
  assert.equal(ui.status.dataset.state, 'saved');
  assert.equal(ui.submissions.length, 1);
});

test('reverting to the acknowledged value clears dirty status without an unnecessary save', async () => {
  const ui = review();
  ui.edit('已确认的版本');
  ui.tick(700);
  await settle();
  ui.edit('临时修改');
  ui.edit('已确认的版本');
  ui.tick(700);
  await settle();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.status.dataset.state, 'saved');
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, false);
});

test('edits made while a native confirmation is navigating still receive unload protection', async () => {
  const ui = review();
  ui.edit('提交时的内容');
  ui.form.requestSubmit(ui.confirm);
  await settle();
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, false);
  ui.edit('提交发出后才修改的内容');
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, true);
});

for (const target of ['confirm', 'create']) test(`unchanged ${target} proceeds with the original native submission`, async () => {
  const ui = review();
  // Input events may fire even when autofill or selectOption leaves values equal.
  ui.edit('跟进报价');
  ui.form.requestSubmit(ui[target]);
  // Browsers may reject requestSubmit() during the original submit event's
  // microtask checkpoint, so a clean form must keep the original submission.
  assert.equal(ui.submissions.length, 1);
  assert.equal(ui.submissions[0].submitter, ui[target]);
  assert.equal(ui.requests.length, 0);
  ui.form.requestSubmit(ui[target]);
  await settle();
  assert.equal(ui.submissions.length, 1);
  assert.equal(ui.requests.length, 0);
});

test('saved submission resumes in a later task and saves edits made before that task', async () => {
  const ui = review(undefined, {pauseReplay: true});
  ui.edit('等待保存的内容');
  ui.form.requestSubmit(ui.create);
  await settle();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.submissions.length, 0);
  ui.form.requestSubmit(ui.create);
  ui.edit('重放提交前的最后修改');
  ui.tick(0);
  await settle();
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].options.body.get('task_0_title'), '重放提交前的最后修改');
  assert.equal(ui.submissions.length, 0);
  ui.tick(0);
  await settle();
  assert.equal(ui.submissions.length, 1);
  assert.equal(ui.submissions[0].submitter, ui.create);
  assert.ok(ui.submissions[0].fields.some(([name, value]) => name === 'review_version' && value === '2'));
});

for (const newer of ['等待回包时又有输入', '跟进报价']) test(`review retries the unacknowledged form before saving ${newer}`, async () => {
  let serverVersion = 0;
  let committed = null;
  const ui = review((_url, {body}, count) => {
    const version = Number(body.get('review_version'));
    const title = body.get('task_0_title');
    if (version === serverVersion) { serverVersion++; committed = title; }
    else if (version + 1 !== serverVersion || title !== committed) {
      return Promise.resolve(response(serverVersion, {ok: false, status: 409,
        json: async () => ({ok: false, message: '草稿已在其他页面修改'})}));
    }
    if (count === 1) return Promise.reject(new Error('response lost after commit'));
    return Promise.resolve(response(serverVersion));
  });
  ui.edit('第一版'); ui.tick(700);
  ui.edit(newer);
  await settle();
  assert.equal(ui.version.value, '0');
  assert.equal(ui.status.dataset.state, 'failed');
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, true);
  ui.form.requestSubmit(ui.confirm);
  await settle();
  assert.equal(ui.status.dataset.state, 'saved');
  assert.equal(ui.version.value, '2');
  assert.equal(committed, newer);
  assert.deepEqual(ui.requests.map(({options: {body}}) => [body.get('review_version'), body.get('task_0_title')]),
    [['0', '第一版'], ['0', '第一版'], ['1', newer]]);
  assert.equal(ui.submissions.length, 1);
  assert.equal(fire(ui.window, 'beforeunload').defaultPrevented, false);
});

test('a rejected review draft saves corrected form values on retry', async () => {
  const ui = review((_url, {body}) => Promise.resolve(body.get('task_0_title') === '无效输入'
    ? response(0, {ok: false, status: 400, json: async () => ({ok: false, message: '请修正输入'})})
    : response(1)));
  ui.edit('无效输入'); ui.tick(700);
  await settle();
  assert.equal(ui.status.dataset.state, 'failed');
  ui.edit('修正后输入'); ui.form.requestSubmit(ui.save);
  await settle();
  assert.equal(ui.status.dataset.state, 'saved');
  assert.equal(ui.version.value, '1');
  assert.equal(ui.requests[1].options.body.get('task_0_title'), '修正后输入');
});
