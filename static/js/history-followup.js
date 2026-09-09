(function (root) {
  'use strict';
  const {SaveQueue} = typeof module !== 'undefined' ? require('./meeting-workspace.js') : root.MeetingWorkspace;

  function createFollowupGuard({version, getState, valid, save, onVersion = () => {}, onStatus = () => {}}) {
    const queue = new SaveQueue({version, getState, save,
      apply: () => onVersion(queue.version), onStatus});
    return {
      mark: () => queue.mark(),
      get dirty() { return queue.dirty; },
      async flush() {
        if (queue.dirty && !valid()) { onStatus('invalid', '请补齐本次答复后暂存，输入仍保留在本页。'); return false; }
        return queue.flush();
      },
      async navigate(url, navigate) {
        if (!await this.flush()) return false;
        navigate(url); return true;
      },
    };
  }

  async function saveFollowup(url, payload, timeoutMs = 15000) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const body = new URLSearchParams({...payload.state, version: payload.version});
      const response = await fetch(url, {method:'POST', body, signal:controller.signal,
        headers:{Accept:'application/json', 'X-CSRFToken':payload.state.csrfmiddlewaretoken}});
      if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('未收到有效暂存确认，输入仍保留在本页。');
      const data = await response.json();
      if (!response.ok || !data.ok) throw Object.assign(new Error(data.error || '暂存失败，输入仍保留在本页。'),
        {conflict:response.status === 409, rejected:response.status === 400});
      return data;
    } finally { clearTimeout(timeout); }
  }

  function mount() {
    const form = document.querySelector('[data-followup-form]');
    if (!form) return;
    const status = document.querySelector('[data-followup-save-status]');
    const guard = createFollowupGuard({version:Number(form.elements.version.value),
      getState:()=>{const state=Object.fromEntries(new FormData(form)); delete state.version; return state;},
      valid:()=>form.reportValidity(), save:payload=>saveFollowup(form.action, payload),
      onVersion:version=>{form.elements.version.value=version;},
      onStatus:(state,error)=>{status.textContent=error || ({dirty:'有未暂存答复，离开本页前会先保存。',saving:'正在暂存答复…',saved:'答复已暂存，可以继续询问。',conflict:'草稿已被其他页面修改，请先保留本页输入并重新核对。'}[state] || '暂存失败，请重试。');},
    });
    if (form.dataset.followupDirty === 'true') guard.mark();
    form.addEventListener('input',()=>guard.mark());
    form.addEventListener('change',()=>guard.mark());
    form.addEventListener('submit',async event=>{event.preventDefault(); if (!guard.dirty) guard.mark(); await guard.flush();});
    document.addEventListener('click',event=>{
      const link=event.target.closest('a[href]');
      if (!link || link.target==='_blank' || link.hasAttribute('download') || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.getAttribute('href').startsWith('#') || !guard.dirty) return;
      event.preventDefault(); guard.navigate(link.href,url=>root.location.assign(url));
    },true);
    root.addEventListener('beforeunload',event=>{if (guard.dirty) {event.preventDefault(); event.returnValue='';}});
  }
  if (typeof module !== 'undefined') module.exports={createFollowupGuard,saveFollowup};
  else root.FollowupDraft={createFollowupGuard,saveFollowup};
  if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded',mount);
})(typeof window !== 'undefined' ? window : globalThis);
