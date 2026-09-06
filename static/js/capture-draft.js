document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-capture-draft]");
  const saved = document.querySelector("[data-capture-saved]");
  const userId = (form || saved)?.dataset.userId;
  if (!userId) return;
  const key = `meeting-progress-tracker:capture:v1:${userId}`;
  const pendingKey = `${key}:pending`;
  const confirmedKey = `${key}:confirmed`;
  if (saved) {
    try {
      const draft = JSON.parse(window.localStorage.getItem(key) || "null");
      const pending = window.sessionStorage.getItem(pendingKey);
      if (saved.dataset.captureToken && pending === saved.dataset.captureToken) {
        window.sessionStorage.setItem(confirmedKey, pending);
        if (draft?.token === pending) window.localStorage.removeItem(key);
        window.sessionStorage.removeItem(pendingKey);
      }
    } catch (_) { /* Retain the draft when successful cleanup cannot be verified. */ }
  }
  if (!form) return;
  const names = ["title", "meeting_date", "raw_text"];
  const fields = Object.fromEntries(names.map(name => [name, form.querySelector(`[name='${name}']`)]));
  const defaultDate = form.dataset.defaultDate || fields.meeting_date.value;
  const status = form.querySelector("[data-capture-status]");
  const token = form.querySelector("[name='capture_draft_token']");
  const buttons = Array.from(form.querySelectorAll("button[type='submit']"));
  const buttonLabels = buttons.map(button => button.textContent);
  const values = () => Object.fromEntries(names.map(name => [name, fields[name].value]));
  let lastValues;
  let unsaved = form.dataset.bound === "true";
  let submitting = false;
  const warn = () => {
    status.textContent = "此浏览器无法暂存，当前修改尚未保存。请保存会议后再离开。";
  };
  const readDraft = () => {
    const stored = window.localStorage.getItem(key);
    if (!stored) return null;
    const draft = JSON.parse(stored);
    if (!draft || !names.every(name => typeof draft[name] === "string") ||
        typeof draft.token !== "string") throw new Error("Invalid capture draft");
    return draft;
  };
  const restore = (draft) => {
    names.forEach(name => { fields[name].value = draft[name]; });
    token.value = draft.token;
    lastValues = JSON.stringify(values());
    unsaved = false;
    status.textContent = "已恢复暂存内容 · 已暂存到此浏览器";
  };
  const reset = () => {
    fields.title.value = "";
    fields.raw_text.value = "";
    fields.meeting_date.value = defaultDate;
    token.value = "";
    lastValues = undefined;
    unsaved = false;
  };
  const persist = () => {
    const current = values();
    try {
      if (JSON.stringify(current) !== lastValues ||
          window.localStorage.getItem(key) !== JSON.stringify({...current, token: token.value})) {
        token.value = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        window.localStorage.setItem(key, JSON.stringify({...current, token: token.value}));
        lastValues = JSON.stringify(current);
      }
      unsaved = false;
      status.textContent = "已暂存到此浏览器";
      return true;
    } catch (_) {
      unsaved = true;
      warn();
      return false;
    }
  };
  try {
    if (form.dataset.bound === "true") persist();
    else {
      const draft = readDraft();
      if (draft) restore(draft);
    }
  } catch (_) { warn(); }
  form.addEventListener("input", persist);
  form.addEventListener("change", persist);
  const clear = form.querySelector("[data-capture-clear]");
  clear.hidden = false;
  clear.addEventListener("click", () => {
    if (!window.confirm("清空当前输入和此账号在本浏览器中的暂存？此操作无法撤销。")) return;
    try {
      window.localStorage.removeItem(key);
    } catch (_) {
      status.textContent = "无法清除浏览器暂存，当前输入已保留。";
      return;
    }
    try { window.sessionStorage.removeItem(pendingKey); } catch (_) {}
    reset();
    status.textContent = "已清空输入和浏览器暂存";
    fields.title.focus();
  });
  form.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    submitting = true;
    if (persist()) {
      try { window.sessionStorage.setItem(pendingKey, token.value); }
      catch (_) { /* Saving the meeting remains available without session storage. */ }
    }
  });
  window.addEventListener("beforeunload", (event) => {
    if (unsaved && !submitting) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  window.addEventListener("pageshow", (event) => {
    submitting = false;
    if (!event.persisted) return;
    form.dataset.submitting = "false";
    buttons.forEach((button, index) => { button.disabled = false; button.textContent = buttonLabels[index]; });
    form.querySelectorAll("input").forEach(input => { if (input.name === "intent") input.remove(); });
    try {
      const draft = readDraft();
      // Only the success page can confirm this cached form's submission.
      const confirmed = window.sessionStorage.getItem(confirmedKey);
      if (token.value && confirmed === token.value) {
        if (draft && draft.token !== confirmed) restore(draft);
        else { reset(); status.textContent = "本次会议已保存，可以开始新记录。"; }
      } else if (token.value && draft?.token !== token.value) {
        unsaved = true;
        status.textContent = "浏览器暂存已在其他页面变更，当前输入尚未暂存；继续编辑会更新暂存。";
      }
    } catch (_) { unsaved = Boolean(fields.title.value || fields.raw_text.value); warn(); }
  });
});
