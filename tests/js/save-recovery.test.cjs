const assert = require('node:assert/strict');
const test = require('node:test');
const {SaveQueue, requestJSON} = require('../../static/js/meeting-workspace.js');

for (const loss of ['network', 'malformed-body', 'body-timeout']) test(`manual save recovers a committed ${loss} before saving later local input`, async () => {
  let local = {title: '第一版'};
  let server = {version: 0, state: {title: '原始内容'}};
  const sent = [];
  const visited = [];
  const originalFetch = global.fetch;
  global.fetch = async (_url, {body}) => {
    const data = JSON.parse(body);
    sent.push(data);
    const conflict = data.version !== server.version &&
      !(data.version + 1 === server.version && JSON.stringify(data.state) === JSON.stringify(server.state));
    if (data.version === server.version) server = {version: data.version + 1, state: data.state};
    if (sent.length === 1) {
      local.title = '等待回包时继续输入'; queue.mark();
      if (loss === 'network') throw new Error('response lost after commit');
    }
    return {ok: !conflict, status: conflict ? 409 : 200, headers: {get: () => 'application/json'},
      json: () => {
        if (sent.length === 1 && loss === 'malformed-body') return Promise.reject(new SyntaxError('truncated JSON'));
        if (sent.length === 1 && loss === 'body-timeout') return new Promise(() => {});
        return Promise.resolve({ok: !conflict, ...server, error: conflict ? 'another revision' : undefined});
      }};
  };
  const queue = new SaveQueue({version: 0, getState: () => local, apply: value => { local = value; },
    save: body => requestJSON('/save', body, 'csrf', 10)});
  try {
    queue.mark();
    assert.equal(await queue.navigate('/preview', url => visited.push(url)), false);
    assert.equal(queue.version, 0);
    assert.equal(local.title, '等待回包时继续输入');
    assert.equal(queue.dirty, true);
    assert.deepEqual(visited, []);
    assert.equal(await queue.navigate('/preview', url => visited.push(url)), true);
    assert.deepEqual(sent.map(({version, state}) => [version, state.title]),
      [[0, '第一版'], [0, '第一版'], [1, '等待回包时继续输入']]);
    assert.equal(server.state.title, '等待回包时继续输入');
    assert.equal(queue.version, 2);
    assert.equal(queue.dirty, false);
    assert.deepEqual(visited, ['/preview']);
  } finally { global.fetch = originalFetch; }
});

test('a rejected manual draft can save corrected input without replaying the invalid snapshot', async () => {
  let local = {title: ''};
  const sent = [];
  const originalFetch = global.fetch;
  global.fetch = async (_url, {body}) => {
    const data = JSON.parse(body); sent.push(data);
    const valid = Boolean(data.state.title);
    return {ok: valid, status: valid ? 200 : 400, headers: {get: () => 'application/json'},
      json: async () => valid ? {ok: true, version: 1, state: data.state} : {ok: false, error: '请填写主题'}};
  };
  const queue = new SaveQueue({version: 0, getState: () => local, apply: value => { local = value; },
    save: body => requestJSON('/save', body, 'csrf')});
  try {
    queue.mark(); assert.equal(await queue.flush(), false);
    local.title = '修正后的主题'; queue.mark();
    assert.equal(await queue.flush(), true);
    assert.deepEqual(sent.map(({version, state}) => [version, state.title]), [[0, ''], [0, '修正后的主题']]);
    assert.equal(queue.dirty, false);
  } finally { global.fetch = originalFetch; }
});
