function setupReviewDraft() {
  const form = document.querySelector("[data-review-form]");
  if (!form || !form.dataset.draftSaveUrl) return;
  const status = form.querySelector("[data-draft-status]");
  const version = form.querySelector("input[name='review_version']");
  if (!status || !version) return;
  const initialStatus = status.textContent;
  let debounce = null;
  let saving = null;
  let unacknowledged = null;
  let leaving = false;
  let bypass = false;
  let nativeSubmitting = false;
  let uncertain = false;
  let halted = false;
  let acknowledged = false;
  let submittedControls = [];
  let submittedInputs = null;
  let submitterName = "";
  const payload = () => {
    const body = new FormData(form);
    body.delete("destination");
    return body;
  };
  const fingerprint = body => JSON.stringify([...body].filter(([name]) => name !== "review_version"));
  let savedFingerprint = fingerprint(payload());
  const isDirty = () => fingerprint(payload()) !== savedFingerprint;
  const setStatus = (state, message) => {
    status.dataset.state = state;
    status.textContent = message;
  };
  const failure = (message) => {
    const error = new Error(message);
    error.draftMessage = message;
    return error;
  };
  const readJSON = async (response, submittedVersion) => {
    if (!(response.headers.get("content-type") || "").includes("application/json")) {
      throw failure(response.redirected || response.status === 401 || response.status === 403
        ? "登录状态已失效。请先复制修改内容，在新标签页重新登录后再重试。"
        : `服务器返回了无法确认的结果（HTTP ${response.status}）。`);
    }
    const data = await response.json();
    if (!response.ok || data?.ok !== true) {
      const error = failure(data?.message || `暂存失败（HTTP ${response.status}）。`);
      error.conflict = response.status === 409;
      error.rejected = response.status === 400;
      throw error;
    }
    if (!Number.isSafeInteger(data.version) || data.version <= submittedVersion ||
        typeof data.saved_at !== "string" || Number.isNaN(Date.parse(data.saved_at))) {
      throw failure("服务器未返回有效的暂存确认。");
    }
    return data;
  };
  const request = async (body) => {
    const controller = new AbortController();
    let deadline;
    const timeout = new Promise((_, reject) => {
      deadline = window.setTimeout(() => {
        controller.abort();
        reject(failure("连接超时，暂时无法确认修改是否已保存。"));
      }, 10000);
    });
    try {
      // The deadline covers both response headers and JSON body reading.
      return await Promise.race([
        fetch(form.dataset.draftSaveUrl, {
          method: "POST", body, signal: controller.signal,
          headers: {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
        }).then(response => readJSON(response, Number(body.get("review_version")))),
        timeout,
      ]);
    } finally { window.clearTimeout(deadline); }
  };
  const save = async (body) => {
    const snapshot = fingerprint(body);
    setStatus("saving", "正在暂存到服务器…");
    try {
      const data = await request(body);
      version.value = String(data.version);
      savedFingerprint = snapshot;
      unacknowledged = null;
      acknowledged = true;
      uncertain = false;
      if (isDirty()) setStatus("dirty", "仍有修改尚未暂存，即将继续保存…");
      else setStatus("saved", "已暂存到服务器");
      return true;
    } catch (error) {
      if (error.rejected) unacknowledged = null;
      uncertain = !error.rejected;
      halted = Boolean(error.conflict);
      window.clearTimeout(debounce);
      debounce = null;
      setStatus("failed", halted
        ? `${error.draftMessage}。已停止自动暂存，请先复制当前修改，再重新打开最新草稿核对。`
        : `${error.draftMessage || "暂存失败。"} 修改仍保留在当前页面，请检查网络后点击“暂存草稿”重试，或先复制内容备份。`);
      return false;
    }
  };
  const flush = async (force = false) => {
    window.clearTimeout(debounce);
    debounce = null;
    while (true) {
      if (halted) return false;
      if (saving) {
        if (!await saving) return false;
        force = false;
        continue;
      }
      if (!isDirty() && !uncertain && !force) return true;
      force = false;
      // A failed response may still have committed. Confirm the same form body
      // first, then send any edits made while that save was outstanding.
      if (!unacknowledged) unacknowledged = payload();
      const request = save(unacknowledged);
      saving = request;
      const success = await request;
      if (saving === request) saving = null;
      if (!success) return false;
    }
  };
  const onEdit = () => {
    window.clearTimeout(debounce);
    if (halted) return;
    if (nativeSubmitting) {
      if (isDirty()) setStatus("dirty", "提交发出后又有新修改，请保留当前页面并先复制内容备份。");
      return;
    }
    if (!isDirty() && !saving && !uncertain) {
      setStatus(acknowledged ? "saved" : "idle", acknowledged ? "已暂存到服务器" : initialStatus);
      return;
    }
    setStatus(saving ? "saving" : "dirty", saving ? "正在暂存，最新修改会接着保存…" : "有修改尚未暂存，即将自动保存…");
    debounce = window.setTimeout(() => { flush(); }, 700);
  };
  form.addEventListener("input", onEdit);
  form.addEventListener("change", onEdit);
  const prepareNativeSubmission = (button) => {
    nativeSubmitting = true;
    submittedControls = Array.from(form.querySelectorAll("button[type='submit']"), button => ({
      button, disabled: button.disabled, label: button.textContent,
    }));
    submittedInputs = new Set(form.querySelectorAll("input"));
    submitterName = button?.name || "";
  };
  // Capture runs before app.js's submit-once handler, regardless of script order.
  form.addEventListener("submit", async (event) => {
    const button = event.submitter || form.querySelector("[data-review-submit]");
    if (bypass) {
      bypass = false;
      prepareNativeSubmission(button);
      return;
    }
    const manualSave = button?.matches("[data-draft-save]");
    // A clean form can submit now. Re-requesting it in a microtask while the
    // browser is still firing this submit event can be silently ignored.
    if (!manualSave && !nativeSubmitting && !leaving && !halted && !saving && !uncertain && !isDirty()) {
      prepareNativeSubmission(button);
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    if (nativeSubmitting || leaving) return;
    if (manualSave) {
      await flush(true);
      return;
    }
    leaving = true;
    try {
      while (true) {
        if (!await flush()) return;
        // Exit the original submit event before requesting another submission,
        // including when the last save was already resolved or answered quickly.
        await new Promise(resolve => window.setTimeout(resolve, 0));
        if (halted) return;
        // An edit may arrive while waiting for the next browser task.
        if (isDirty() || saving || uncertain) continue;
        bypass = true;
        form.requestSubmit(button);
        break;
      }
    } finally {
      // requestSubmit may fail native validation without emitting submit.
      bypass = false;
      leaving = false;
    }
  }, true);
  document.addEventListener("click", async (event) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey ||
        (event.button !== undefined && event.button !== 0)) return;
    const link = event.target.closest?.("a[href]");
    if (!link || link.getAttribute("download") !== null) return;
    const target = link.getAttribute("target");
    if (target && !["_self", "_parent", "_top"].includes(target)) return;
    const href = link.getAttribute("href");
    if (!href || href.startsWith("#")) return;
    const destination = new URL(href, window.location.href);
    if (!["http:", "https:"].includes(destination.protocol)) return;
    const current = new URL(window.location.href);
    if (destination.hash && destination.origin === current.origin &&
        destination.pathname === current.pathname && destination.search === current.search) return;
    if (!isDirty() && !saving && !uncertain) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (leaving || nativeSubmitting) return;
    leaving = true;
    try {
      if (await flush()) window.location.assign(destination.href);
    } finally { leaving = false; }
  }, true);
  window.addEventListener("beforeunload", (event) => {
    if (!isDirty() && !saving && !uncertain) return;
    event.preventDefault();
    event.returnValue = "";
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    nativeSubmitting = false;
    leaving = false;
    bypass = false;
    delete form.dataset.submitting;
    submittedControls.forEach(({button, disabled, label}) => {
      button.disabled = disabled;
      button.textContent = label;
    });
    // submit-once inserts an intent input before disabling the clicked button.
    // Remove only that newly inserted input when restoring the cached page.
    if (submittedInputs && submitterName) form.querySelectorAll("input").forEach(input => {
      if (!submittedInputs.has(input) && input.type === "hidden" && input.name === submitterName) input.remove();
    });
    submittedControls = [];
    submittedInputs = null;
    if (isDirty() && !halted) onEdit();
  });
}

function setupDraftPlanning() {
  document.querySelectorAll("[data-planning-review]").forEach(panel => {
    const row = panel.closest("[data-review-row]");
    const fields = panel.querySelectorAll("[data-plan-field]");
    const planned = panel.querySelector("[data-plan-field='planned_for']");
    const due = row.querySelector("input[name$='_due_date']");
    const warning = panel.querySelector("[data-plan-warning]");
    const showWarning = () => {
      const iso = value => /^\d{4}-\d{2}-\d{2}$/.test(value || "");
      warning.hidden = !(iso(planned.value) && iso(due?.value) && planned.value > due.value);
    };
    fields.forEach(field => {
      const marker = panel.querySelector(`[data-plan-edited-for='${field.dataset.planField}']`);
      if (marker) marker.value = field.dataset.planEdited === "true" ? "true" : "false";
      ["input", "change"].forEach(event => field.addEventListener(event, () => {
        field.dataset.planEdited = "true";
        if (marker) marker.value = "true";
        showWarning();
      }));
    });
    due?.addEventListener("input", showWarning);
    let plans = {};
    try { plans = JSON.parse(panel.querySelector("script[type='application/json']").textContent); } catch (_) { /* Server form remains usable. */ }
    const action = row.querySelector("[data-task-action]");
    const target = row.querySelector("[data-existing-field] select");
    const sync = () => {
      const values = action?.value === "update" ? plans[target?.value] : {phase_id: "", planned_for: ""};
      if (values) fields.forEach(field => {
        if (field.dataset.planEdited !== "true") field.value = values[field.dataset.planField] || "";
      });
      showWarning();
    };
    target?.addEventListener("change", sync);
    action?.addEventListener("change", sync);
    showWarning();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupDraftPlanning();
  setupReviewDraft();
});
