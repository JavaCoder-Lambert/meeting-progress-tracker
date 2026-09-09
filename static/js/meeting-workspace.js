(function (root) {
  'use strict';
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const hasBaseline = (item) => Boolean(item.baseline_values?.status && Number.isInteger(item.baseline_values.progress));
  const scheduleAfterDeadline = (item) => Boolean(item.status !== 'done' && item.planned_for && item.due_date && item.planned_for > item.due_date);
  const uuid = () => {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    return Array.from(bytes, (value, index) => ([4, 6, 8, 10].includes(index) ? '-' : '') + value.toString(16).padStart(2, '0')).join('');
  };
  class Workspace {
    constructor(state, makeId = uuid) {
      this.state = state; this.uuid = makeId; this.person = null;
      this.pendingOnly = false; this.activeId = undefined;
    }
    counts(person = this.person) {
      const items = this.state.items.filter((item) => !person || item.person_id === person);
      const tasks = items.filter((item) => item.kind === 'task');
      const asked = tasks.filter((item) => item.recorded).length;
      return {total: tasks.length, asked, pending: tasks.length - asked,
        added: items.filter((item) => item.kind !== 'task').length};
    }
    records() {
      return this.state.items.filter((item) => (!this.person || item.person_id === this.person) &&
        (!this.pendingOnly || (item.kind === 'task' && !item.recorded) || item.id === this.activeId));
    }
    selectPerson(person) { this.person = person; this.activeId = undefined; }
    focusRecord(id) {
      const item = this.state.items.find((entry) => entry.id === id);
      if (!item) return false;
      this.activeId = id;
      if (this.person && item.person_id !== this.person) this.person = null;
      if (item.kind !== 'task') this.pendingOnly = false;
      return true;
    }
    nextPending() {
      const items = this.state.items.filter((item) => !this.person || item.person_id === this.person);
      const index = items.findIndex((item) => item.id === this.activeId);
      return [...items.slice(index + 1), ...items.slice(0, index)].find(
        (item) => item.kind === 'task' && !item.recorded)?.id || null;
    }
    add(kind, project = null) {
      const item = {id: this.uuid(), kind, task_id: null, project_id: project, phase_id: null, person_id: this.person,
        title: '', status: 'not_started', progress: '', due_date: '', planned_for: '', completed_work: '',
        next_step: '', content: '', recorded: kind !== 'task', baseline: ''};
      this.state.items.push(item); return item;
    }
    addTask(task) {
      const existing = this.state.items.find((item) => item.task_id === task.id);
      if (existing) return existing;
      const item = this.add('task', task.project_id);
      Object.assign(item, {task_id: task.id, title: task.title, status: task.status, progress: task.progress,
        due_date: task.due_date || '', planned_for: task.planned_for || '', baseline: task.baseline,
        baseline_values: {status: task.status, progress: task.progress, title: task.title, project_name: task.project_name,
          assignee_name: task.assignee_name, due_date: task.due_date || '', planned_for: task.planned_for || ''}});
      return item;
    }
    noChange(id) {
      const item = this.state.items.find((entry) => entry.id === id);
      if (!item || !hasBaseline(item)) return false;
      item.status = item.baseline_values.status; item.progress = item.baseline_values.progress; item.recorded = true;
      return true;
    }
    update(id, key, value) {
      const item = this.state.items.find((entry) => entry.id === id);
      if (!item) return null;
      if (item.kind === 'new_task' && key === 'project_id' && item.project_id !== value) item.phase_id = null;
      item[key] = value;
      if (item.kind === 'task' && ['status', 'progress', 'completed_work', 'next_step', 'due_date', 'planned_for', 'content', 'title'].includes(key)) item.recorded = true;
      return item;
    }
  }
  class SaveQueue {
    constructor({version, getState, apply, save, onStatus = () => {}}) {
      Object.assign(this, {version, getState, apply, save, onStatus});
      this.revision = 0; this.saved = 0; this.pending = null; this.conflict = false; this.unacknowledged = null;
    }
    get dirty() { return this.revision !== this.saved; }
    mark() { this.revision++; this.onStatus(this.conflict ? 'conflict' : 'dirty'); }
    async flush() {
      if (this.conflict) return false;
      if (this.pending) return this.pending;
      this.pending = this.drain();
      try { return await this.pending; } finally { this.pending = null; }
    }
    async drain() {
      while (this.dirty) {
        // Keep the exact outstanding save across failures; newer local edits
        // must wait until the server has acknowledged this revision.
        if (!this.unacknowledged) this.unacknowledged = {
          revision: this.revision, body: {version: this.version, state: clone(this.getState())},
        };
        const {revision, body} = this.unacknowledged;
        this.onStatus('saving');
        try {
          const data = await this.save(body);
          if (data.confirmed) throw Object.assign(new Error('会议已经确认，请保留本页输入并核对最新内容。'), {conflict: true});
          if (!Number.isInteger(data.version) || data.version <= this.version || !data.state || typeof data.state !== 'object' || Array.isArray(data.state)) throw new Error('未收到有效的暂存确认，内容仍在本页，请重试。');
          this.version = data.version; this.saved = revision;
          this.unacknowledged = null;
          if (this.revision === revision) this.apply(data.state);
        } catch (error) {
          if (error.rejected) this.unacknowledged = null;
          this.conflict = Boolean(error.conflict);
          this.onStatus(this.conflict ? 'conflict' : 'failed', error.message); return false;
        }
      }
      this.onStatus('saved'); return true;
    }
    async navigate(url, navigate) { if (!await this.flush()) return false; navigate(url); return true; }
  }
  function once() { let accepted = false; return () => { if (accepted) return false; accepted = true; return true; }; }
  async function requestJSON(url, body, csrf, timeoutMs = 15000) {
    const controller = new AbortController(); let timer;
    try {
      return await Promise.race([fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json',
        Accept: 'application/json', 'X-CSRFToken': csrf}, body: JSON.stringify(body), signal: controller.signal}).then(async (response) => {
        if (!(response.headers.get('content-type') || '').includes('application/json')) throw new Error('连接异常或登录已过期。请保留本页内容，重新登录后重试。');
        const data = await response.json();
        if (!response.ok || !data.ok) throw Object.assign(new Error(data.error || '保存失败，请重试。'), {
          conflict: response.status === 409 || data.conflict, rejected: response.status === 400,
        });
        return data;
      }), new Promise((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new Error('连接超时，内容仍在本页，请重试暂存。')); }, timeoutMs); })]);
    } finally { clearTimeout(timer); }
  }
  function mount() {
    document.querySelectorAll('[data-meeting-confirm], [data-meeting-create]').forEach((form) => {
      let accept = once();
      const button = form.querySelector('button[type="submit"]');
      const original = {disabled: button.disabled, label: button.textContent};
      form.addEventListener('submit', (event) => {
        if (original.disabled || !accept()) { event.preventDefault(); return; }
        button.disabled = true; button.textContent = form.getAttribute('data-meeting-create') !== null ? '正在创建…' : '正在确认…';
      });
      window.addEventListener('pageshow', (event) => {
        if (!event.persisted) return;
        accept = once(); button.disabled = original.disabled; button.textContent = original.label;
      });
    });
    const page = document.querySelector('[data-meeting-workspace]');
    if (!page) return;
    const data = JSON.parse(document.getElementById('meeting-session-data').textContent);
    if (data.confirmed) return;
    const catalog = JSON.parse(document.getElementById('meeting-catalog-data').textContent);
    const urls = data.urls;
    const w = new Workspace(data.state);
    const find = (selector) => page.querySelector(selector);
    const csrf = find('[name="csrfmiddlewaretoken"]').value;
    let debounce;
    const node = (tag, text, className) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n; };
    const button = (text, action, className = '') => { const n = node('button', text, className); n.type = 'button'; n.addEventListener('click', action); return n; };
    const name = (kind, id) => catalog[kind].find((x) => x.id === id)?.name || '未指定';
    const queue = new SaveQueue({version: data.version, getState: () => w.state, apply: (state) => { w.state = state; },
      save: (payload) => requestJSON(urls.save, payload, csrf), onStatus: (state, error) => {
        const labels = {dirty: '有修改 · 等待暂存', saving: '正在暂存到服务器…', saved: '所有修改已暂存到服务器', failed: '暂存失败 · 内容仍在本页', conflict: '版本冲突 · 已停止自动暂存'};
        find('[data-save-status]').textContent = labels[state]; find('[data-save-status]').dataset.state = state;
        const alert = find('[data-save-error]'); alert.hidden = !['failed', 'conflict'].includes(state);
        alert.textContent = state === 'conflict' ? '本会议已在其他页面修改。为避免覆盖，已保留你的本页输入并停止保存。请先复制或下载本页草稿，再刷新核对最新内容。' : error || '';
        if (state === 'conflict') alert.append(button('下载本页草稿', () => {
          const url = URL.createObjectURL(new Blob([JSON.stringify(w.state, null, 2)], {type: 'application/json'}));
          const link = node('a'); link.href = url; link.download = '会议本页草稿.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
        }, 'button'));
        find('[data-save]').disabled = state === 'saving' || state === 'conflict';
      }});
    const changed = () => { queue.mark(); clearTimeout(debounce); debounce = setTimeout(() => queue.flush(), 700); renderPeople(); updateHeader(); };
    const select = (options, value, onChange, empty = '未指定') => {
      const input = node('select'); input.append(new Option(empty, ''));
      options.forEach((x) => input.append(new Option(x.name ?? x.label, x.id ?? x.value)));
      if (value && !options.some((x) => String(x.id ?? x.value) === String(value))) input.append(new Option('关联已失效，请重新选择', value));
      input.value = value ?? ''; input.addEventListener('change', () => onChange(input.value)); return input;
    };
    let fieldIndex = 0;
    const field = (label, input) => { const wrapper = node('div', undefined, 'field'); const caption = node('label', label); input.id = `mw-field-${++fieldIndex}`; caption.htmlFor = input.id; wrapper.append(caption, input); return wrapper; };
    const textInput = (value, onChange, type = 'text') => {
      const input = node(type === 'textarea' ? 'textarea' : 'input'); if (type !== 'textarea') input.type = type;
      if (type === 'textarea') input.rows = 2; input.value = value ?? '';
      input.addEventListener('input', () => onChange(input.value)); return input;
    };
    const updateHeader = () => {
      find('[data-meeting-title]').textContent = w.state.title || '未命名会议';
      find('[data-meeting-date]').textContent = `${w.state.meeting_date} ${w.state.meeting_time || ''}`;
      find('[data-current-person]').textContent = w.person ? name('people', w.person) : '全部人员';
      const count = w.counts();
      find('[data-record-count]').textContent = `已问 ${count.asked} / ${count.total} · 待问 ${count.pending}${count.added ? ` · 新增 ${count.added} 项` : ''}`;
      find('[data-pending-filter]').setAttribute('aria-pressed', String(w.pendingOnly));
    };
    const renderPeople = () => {
      const target = find('[data-people]'); target.replaceChildren();
      const people = catalog.people.filter((person) => w.state.person_ids.includes(person.id) || w.state.items.some((item) => item.person_id === person.id));
      [{id: null, name: '全部人员'}, ...people].forEach((person) => {
        const count = w.counts(person.id);
        const b = button('', () => { w.selectPerson(person.id); renderPeople(); renderTasks(); renderRecords(); updateHeader(); }, 'mw-person');
        b.setAttribute('aria-pressed', String(w.person === person.id));
        b.append(node('span', person.name), node('span', count.total ? `已问 ${count.asked} / ${count.total}` : count.added ? `新增 ${count.added} 项` : '无任务', 'mw-count')); target.append(b);
      });
      find('[data-people-count]').textContent = people.length;
    };
    const renderMetadata = () => {
      const target = find('[data-metadata]'); target.replaceChildren();
      const grid = node('div', undefined, 'mw-fields');
      [['会议主题', 'title', 'text'], ['会议日期', 'meeting_date', 'date'], ['开始时间', 'meeting_time', 'time'], ['议题', 'agenda', 'textarea']].forEach(([label, key, type]) => grid.append(field(label, textInput(w.state[key], (value) => { w.state[key] = value; changed(); }, type))));
      target.append(grid);
      const refs = node('div', undefined, 'mw-fields');
      [['projects', 'project_ids', '关联项目'], ['people', 'person_ids', '汇报人员']].forEach(([kind, key, label]) => {
        const group = node('fieldset', undefined, 'mw-checkboxes'); group.append(node('legend', label));
        if (!catalog[kind].length) group.append(node('p', '暂无，可在左侧快捷创建。', 'muted'));
        catalog[kind].forEach((entry) => {
          const input = node('input'); input.type = 'checkbox'; input.checked = w.state[key].includes(entry.id);
          input.addEventListener('change', () => { w.state[key] = input.checked ? [...w.state[key], entry.id] : w.state[key].filter((id) => id !== entry.id); changed(); renderTasks(); });
          const labelNode = node('label'); labelNode.append(input, document.createTextNode(entry.name)); group.append(labelNode);
        }); refs.append(group);
      }); target.append(refs);
    };
    const renderProjectFilter = () => {
      const current = find('#task-project'); const value = current.value;
      current.replaceChildren(new Option('全部项目', ''));
      catalog.projects.forEach((project) => current.append(new Option(project.name, project.id))); current.value = value;
    };
    const renderTasks = () => {
      const target = find('[data-task-options]'); target.replaceChildren();
      const query = find('#task-search').value.toLocaleLowerCase(); const project = Number(find('#task-project').value);
      const tasks = catalog.tasks.filter((task) =>
        (find('#completed-tasks').checked || task.status !== 'done') &&
        (!w.person || find('#all-people-tasks').checked || task.assignee_id === w.person) &&
        (!project || task.project_id === project) &&
        (!w.state.project_ids.length || project || w.state.project_ids.includes(task.project_id)) &&
        `${task.title} ${task.project_name}`.toLocaleLowerCase().includes(query));
      tasks.forEach((task) => {
        const exists = w.state.items.some((item) => item.task_id === task.id);
        const row = node('div', undefined, 'mw-task-option'); const info = node('div'); info.append(node('strong', task.title), node('small', `${task.project_name} · ${task.assignee_name || '未指定负责人'} · ${task.status_label} ${task.progress}%`));
        row.append(info, button(exists ? '查看记录' : '加入记录', () => {
          const item = w.addTask(task); if (!exists) changed();
          w.focusRecord(item.id);
          renderPeople(); renderTasks(); renderRecords(); updateHeader();
          find('.mw-catalog').open = false;
          focusEditor(item.id);
        }, 'button quiet')); target.append(row);
      });
      if (!tasks.length) target.append(node('p', '没有符合条件的任务。可切换人员、调整筛选，或直接新增任务。', 'mw-catalog-empty'));
    };
    const focusEditor = (id) => {
      const card = document.getElementById(`record-${id}`);
      card?.scrollIntoView({block: 'nearest'});
      (card?.querySelector('[data-record-toggle]') || find('[data-pending-filter]')).focus();
    };
    const renderRecords = () => {
      const target = find('[data-records]'); target.replaceChildren();
      const items = w.records();
      if (w.activeId === undefined) w.activeId = (items.find((item) => item.kind === 'task' && !item.recorded) || items[0])?.id || null;
      find('[data-empty]').hidden = items.length > 0;
      find('[data-empty-title]').textContent = w.pendingOnly ? '当前人员没有待问任务' : '从一项任务开始询问';
      find('[data-empty-copy]').textContent = w.pendingOnly ? '可关闭「只看待问」回看记录，或切换人员继续。' : '选择已有任务，或新增一条记录。每次编辑都会自动暂存到服务器。';
      const kinds = {task: '已有任务', new_task: '新增任务', risk: '风险', decision: '待决策', note: '普通纪要'};
      items.forEach((item) => {
        const shell = node('article', undefined, 'mw-record'); shell.id = `record-${item.id}`;
        const expanded = item.id === w.activeId;
        const card = node('div', undefined, 'mw-record-body'); card.id = `record-body-${item.id}`; card.hidden = !expanded;
        const title = node('strong'); const meta = node('span', undefined, 'mw-record-meta');
        const next = node('span', undefined, 'mw-record-next'); const state = node('span', undefined, 'mw-record-state');
        const toggle = button('', () => { w.activeId = expanded ? null : item.id; renderRecords(); focusEditor(item.id); }, 'mw-record-toggle');
        toggle.setAttribute('data-record-toggle', ''); toggle.setAttribute('aria-expanded', String(expanded));
        toggle.setAttribute('aria-controls', card.id);
        const summary = node('span', undefined, 'mw-record-summary'); summary.append(title, meta, next);
        toggle.append(summary, state);
        const refreshSummary = () => {
          title.textContent = item.title || kinds[item.kind];
          const status = catalog.statuses.find((entry) => entry.value === item.status)?.label || '未指定状态';
          meta.textContent = item.kind === 'task' ? `${item.baseline_values?.project_name || name('projects', item.project_id)} · ${name('people', item.person_id)} · ${status} ${item.progress === '' ? '' : `${item.progress}%`}` : `${kinds[item.kind]} · ${name('people', item.person_id)}`;
          next.textContent = item.next_step ? `下一步：${item.next_step}` : '';
          next.hidden = expanded || !item.next_step;
          state.textContent = `${item.kind === 'task' ? item.recorded ? '已问' : '待问' : '新增'} · ${expanded ? '收起' : '展开'}`;
        };
        const set = (key, value) => {
          const current = w.update(item.id, key, value);
          if (current) { item = current; const recorded = card.querySelector('[data-recorded-checkbox]'); if (recorded) recorded.checked = item.recorded;
            const warning = card.querySelector('[data-schedule-warning]'); if (warning) warning.hidden = !scheduleAfterDeadline(item);
            refreshSummary(); changed(); }
        };
        // Collapsed rows use the draft state as their source of truth; only the
        // active row needs form controls, even for a large meeting.
        if (!expanded) { refreshSummary(); shell.append(toggle, card); target.append(shell); return; }
        if (item.kind === 'task') {
          const baseline = hasBaseline(item) ? item.baseline_values : null;
          const statusLabel = baseline && catalog.statuses.find((status) => status.value === baseline.status)?.label;
          card.append(node('p', baseline ? `原状态 ${statusLabel || baseline.status} ${baseline.progress}% · 负责人 ${baseline.assignee_name || '未指定'}` : '原始基线不可用，请先暂存并刷新工作台。不能使用「无变化」。', 'mw-task-baseline'));
          const input = node('input'); input.type = 'checkbox'; input.checked = item.recorded; input.setAttribute('data-recorded-checkbox', '');
          const label = node('label', undefined, 'mw-recorded'); label.append(input, document.createTextNode('已询问，纳入本次确认'));
          input.addEventListener('change', () => set('recorded', input.checked)); card.append(label);
          const quick = node('div', undefined, 'mw-quick');
          [['已完成', 'done'], ['进行中', 'in_progress'], ['受阻', 'blocked'], ['无变化', null]].forEach(([labelText, status]) => {
            const shortcut = button(labelText, () => {
            if (status) set('status', status); else { if (!w.noChange(item.id)) return; changed(); }
            if (status === 'done') set('progress', 100); set('recorded', true); renderRecords(); focusEditor(item.id);
            }); shortcut.disabled = !status && !baseline; quick.append(shortcut);
          }); card.append(quick);
        } else card.append(field(item.kind === 'note' ? '纪要标题 · 选填' : ['risk', 'decision'].includes(item.kind) ? '标题 · 选填，可在下方直接说明' : '任务标题', textInput(item.title, (v) => set('title', v))));
        const refs = node('div', undefined, 'mw-fields');
        let phaseField;
        const renderPhase = () => {
          const phases = (catalog.phases || []).filter((phase) => phase.project_id === item.project_id);
          const input = select(phases, item.phase_id, (v) => set('phase_id', v ? Number(v) : null),
            item.project_id ? '未指定阶段' : '请先选择项目');
          input.disabled = !item.project_id;
          phaseField.replaceChildren(field('项目阶段 · 选填', input));
          if (item.project_id && !phases.length) phaseField.append(node('small', '此项目暂无阶段，可先记录任务。', 'muted'));
        };
        if (item.kind !== 'task') refs.append(field(item.kind === 'note' ? '项目 · 选填' : '关联项目 · 确认时必填', select(catalog.projects, item.project_id, (v) => {
          set('project_id', v ? Number(v) : null);
          if (phaseField) renderPhase();
        })));
        refs.append(field(item.kind === 'task' ? '本次汇报人（不更改负责人）' : '人员', select(catalog.people, item.person_id, (v) => set('person_id', v ? Number(v) : null))));
        if (item.kind !== 'task') card.append(refs);
        if (item.kind === 'new_task') { phaseField = node('div', undefined, 'mw-phase-field'); renderPhase(); card.append(phaseField); }
        if (['task', 'new_task'].includes(item.kind)) {
          const progress = textInput(item.progress, (v) => set('progress', v === '' ? '' : Number(v)), 'number'); progress.min = 0; progress.max = 100;
          const status = select(catalog.statuses, item.status, (v) => { set('status', v); if (v === 'done') { set('progress', 100); progress.value = 100; } }, '请选择状态');
          const grid = node('div', undefined, 'mw-fields'); grid.append(field('状态', status), field('进度 %', progress)); card.append(grid);
          const notes = node('div', undefined, 'mw-fields');
          notes.append(field('本次完成', textInput(item.completed_work, (v) => set('completed_work', v), 'textarea')));
          notes.append(field('下一步', textInput(item.next_step, (v) => set('next_step', v), 'textarea'))); card.append(notes);
          const details = node('details', undefined, 'mw-details'); details.append(node('summary', item.kind === 'task' ? '日期、汇报人与补充说明' : '日期与补充说明'));
          if (item.kind === 'task') details.append(refs);
          const dates = node('div', undefined, 'mw-fields'); dates.append(field('截止日期', textInput(item.due_date, (v) => set('due_date', v), 'date')), field('安排日期', textInput(item.planned_for, (v) => set('planned_for', v), 'date')));
          const warning = node('p', '安排日期晚于交付截止日期，请确认是否需要提前推进。仍可暂存，截止日期不会自动改变。', 'helptext danger');
          warning.setAttribute('data-schedule-warning', ''); warning.setAttribute('role', 'status'); warning.hidden = !scheduleAfterDeadline(item);
          details.append(dates, warning, field('补充说明', textInput(item.content, (v) => set('content', v), 'textarea'))); card.append(details);
        } else {
          card.append(field(item.kind === 'note' ? '纪要内容' : '具体说明 / 需要的支持', textInput(item.content, (v) => set('content', v), 'textarea')));
          if (['risk', 'decision'].includes(item.kind)) {
            const details = node('details', undefined, 'mw-details'); details.append(node('summary', '截止日期 · 选填'));
            details.append(field('期望解决日期', textInput(item.due_date, (v) => set('due_date', v), 'date'))); card.append(details);
          }
        }
        const actions = node('div', undefined, 'mw-record-actions');
        actions.append(button(w.nextPending() ? '下一项待问' : '收起记录', () => {
          w.activeId = w.nextPending(); renderRecords();
          if (w.activeId) focusEditor(w.activeId); else find('[data-pending-filter]').focus();
        }, 'button quiet'));
        actions.append(button('移除本次记录', () => {
          if (!window.confirm('移除这条本次会议记录？尚未确认，不会删除正式任务。')) return;
          w.state.items = w.state.items.filter((entry) => entry.id !== item.id);
          w.activeId = undefined; changed(); renderRecords(); renderTasks();
        }, 'draft-reference-button'));
        card.append(actions); refreshSummary(); shell.append(toggle, card); target.append(shell);
      });
    };
    find('[data-add-reference]').addEventListener('click', async (event) => {
      const kind = find('#reference-kind').value; const input = find('#reference-name'); const status = find('[data-reference-status]');
      if (!input.value.trim()) { status.textContent = '请先填写名称。'; input.focus(); return; }
      event.currentTarget.disabled = true; status.textContent = '正在创建…';
      try {
        const result = await requestJSON(urls.reference, {kind, name: input.value.trim()}, csrf);
        const collection = kind === 'person' ? 'people' : 'projects'; const key = kind === 'person' ? 'person_ids' : 'project_ids';
        if (!catalog[collection].some((x) => x.id === result.item.id)) catalog[collection].push(result.item);
        if (!w.state[key].includes(result.item.id)) w.state[key].push(result.item.id);
        input.value = ''; changed(); renderMetadata(); renderProjectFilter(); renderRecords(); renderTasks();
        status.textContent = `已创建「${result.item.name}」，现在可以在记录中选择。`;
      } catch (error) { status.textContent = error.message; }
      finally { find('[data-add-reference]').disabled = false; }
    });
    find('#reference-kind').addEventListener('change', () => { find('#reference-name').maxLength = find('#reference-kind').value === 'person' ? 80 : 120; });
    page.querySelectorAll('[data-add-kind]').forEach((b) => b.addEventListener('click', () => {
      const item = w.add(b.dataset.addKind, Number(find('#task-project').value) || w.state.project_ids[0] || null);
      w.focusRecord(item.id);
      changed(); renderRecords(); document.getElementById(`record-${item.id}`)?.querySelector('input')?.focus();
    }));
    find('[data-pending-filter]').addEventListener('click', () => {
      w.pendingOnly = !w.pendingOnly; w.activeId = undefined; renderRecords(); updateHeader();
    });
    find('[data-open-catalog]').addEventListener('click', () => {
      find('.mw-catalog').open = true; find('#task-search').focus();
    });
    ['#task-search', '#task-project', '#all-people-tasks', '#completed-tasks'].forEach((selector) => find(selector).addEventListener('input', renderTasks));
    const go = async (url) => { clearTimeout(debounce); await queue.navigate(url, (target) => window.location.assign(target)); };
    find('[data-save]').addEventListener('click', () => { clearTimeout(debounce); queue.flush(); });
    find('[data-preview]').addEventListener('click', () => go(urls.preview));
    find('[data-exit]').addEventListener('click', () => go(urls.list));
    document.addEventListener('click', (event) => {
      const link = event.target.closest('a[href]'); if (!link || link.target === '_blank' || link.hasAttribute('download') || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.getAttribute('href').startsWith('#') || !queue.dirty) return;
      event.preventDefault(); go(link.href);
    }, true);
    window.addEventListener('beforeunload', (event) => { if (queue.dirty) { event.preventDefault(); event.returnValue = ''; } });
    // The global n shortcut navigates directly; route it through durable flush here.
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'n' || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || event.target.closest('input,textarea,select,[contenteditable]')) return;
      const link = document.querySelector('[data-shortcut-new-meeting]'); if (link && queue.dirty) { event.preventDefault(); event.stopImmediatePropagation(); go(link.href); }
    }, true);
    renderMetadata(); renderProjectFilter(); renderPeople(); renderTasks(); renderRecords(); updateHeader();
    find('[data-editor]').hidden = false; find('[data-save-status]').textContent = '所有修改已暂存到服务器';
  }
  const api = {Workspace, SaveQueue, once, requestJSON, scheduleAfterDeadline};
  if (typeof module !== 'undefined') module.exports = api;
  else root.MeetingWorkspace = api;
  if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', mount);
})(typeof window !== 'undefined' ? window : globalThis);
