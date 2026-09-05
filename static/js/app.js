function setupParseForm() {
  const form = document.querySelector("[data-parse-form]");
  if (!form) return;

  const button = form.querySelector("[data-parse-button]");
  const buttonLabel = form.querySelector("[data-button-label]");
  const progress = document.querySelector("[data-parse-progress]");
  const statusBadge = document.querySelector("[data-note-status]");
  const progressTitle = progress.querySelector("[data-progress-title]");
  const progressDetail = progress.querySelector("[data-progress-detail]");
  const elapsed = progress.querySelector("[data-elapsed]");
  let running = false;
  let checking = false;
  let timer = null;
  let startedAt = Date.now();
  const statusUrl = form.dataset.statusUrl;

  const setBusy = () => {
    running = true;
    button.disabled = true;
    button.classList.add("is-loading");
    buttonLabel.textContent = "正在后台解析";
    progress.hidden = false;
    progress.classList.remove("is-error");
    progressTitle.textContent = "正在整理会议内容";
    progressDetail.textContent = "记录已经保存，可以离开或关闭页面，稍后回来查看结果。";
    const draftLink = document.querySelector("[data-draft-link]");
    if (draftLink) draftLink.hidden = true;
  };
  const fail = (message) => {
    window.clearInterval(timer);
    timer = null;
    if (statusBadge) {
      statusBadge.className = "status-badge status-failed";
      statusBadge.textContent = "解析失败";
    }
    progress.classList.add("is-error");
    progressTitle.textContent = "解析未完成";
    progressDetail.textContent = message;
    button.disabled = false;
    button.classList.remove("is-loading");
    buttonLabel.textContent = "重试解析";
    running = false;
  };
  const showState = (data) => {
    if ((data.state === "success" || data.state === "imported") && data.redirect_url) {
      window.clearInterval(timer);
      timer = null;
      running = false;
      progressTitle.textContent = "解析完成，正在打开草稿";
      progressDetail.textContent = "接下来核对任务、负责人和日期。";
      window.location.assign(data.redirect_url);
    } else if (data.state === "failed" || data.state === "not_parsed") {
      fail(data.message || "尚未开始解析，请点击重试。");
    } else {
      if (data.started_at) startedAt = Date.parse(data.started_at) || startedAt;
      progressTitle.textContent = data.state === "queued" ? "等待开始解析" : "正在整理会议内容";
      progressDetail.textContent = data.message || "解析会在后台继续，可以离开页面。";
      if (statusBadge) statusBadge.textContent = data.state === "queued" ? "等待解析" : "解析中";
    }
  };
  const readJSON = async (response) => {
    if (!(response.headers.get("content-type") || "").includes("application/json")) {
      const error = new Error(response.redirected ? "登录已过期，请刷新页面重新登录。" : `暂时无法获取进度（HTTP ${response.status}），正在重新连接。`);
      error.stopPolling = response.redirected || response.status === 401 || response.status === 403;
      throw error;
    }
    const data = await response.json();
    if (!response.ok || !data.ok) {
      const error = new Error(data.message || "解析未完成，请稍后重试。");
      error.stopPolling = true;
      throw error;
    }
    return data;
  };
  const requestJSON = async (url, options) => {
    const controller = new AbortController();
    let deadline;
    const timeout = new Promise((_, reject) => {
      deadline = window.setTimeout(() => {
        controller.abort();
        reject(new Error("连接超时，正在重新获取解析进度。"));
      }, 15000);
    });
    try {
      // Keep the body read under the same deadline as the response headers.
      return await Promise.race([
        fetch(url, {...options, signal: controller.signal}).then(readJSON), timeout,
      ]);
    } finally { window.clearTimeout(deadline); }
  };
  const poll = async () => {
    if (checking || !running || !statusUrl) return;
    checking = true;
    try {
      showState(await requestJSON(statusUrl, {headers: {"Accept": "application/json"}, cache: "no-store"}));
    } catch (error) {
      if (error.stopPolling) fail(error.message);
      else progressDetail.textContent = "连接暂时中断，解析仍在后台继续，正在重新连接。";
    } finally { checking = false; }
  };
  const watch = () => {
    if (timer !== null) return;
    timer = window.setInterval(() => {
      elapsed.textContent = `已等待 ${Math.max(0, Math.floor((Date.now() - startedAt) / 1000))} 秒`;
      poll();
    }, 2000);
    poll();
  };
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (running) return;
    startedAt = Date.now();
    setBusy();
    try {
      showState(await requestJSON(form.action, {
        method: "POST", body: new FormData(form),
        headers: {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
      }));
      if (running) watch();
    } catch (error) {
      if (error.stopPolling || !statusUrl) fail(error.message || "网络连接异常，请稍后重试。");
      else watch(); // A lost response may still have queued the job; only query its state.
    }
  });
  if (["queued", "running"].includes(form.dataset.parseState)) {
    setBusy();
    startedAt = Date.parse(form.dataset.startedAt) || Date.now();
    watch();
  }
}

function setupTaskActions() {
  document.querySelectorAll("[data-task-action]").forEach((action) => {
    const item = action.closest(".review-item");
    const existingField = item.querySelector("[data-existing-field]");
    if (!existingField) return;
    const existingSelect = existingField.querySelector("select");
    const sync = () => {
      const updating = action.value === "update";
      existingField.hidden = !updating;
      existingSelect.disabled = !updating;
      existingSelect.required = updating;
    };
    action.addEventListener("change", sync);
    sync();
  });
}

function setupSubmitOnce() {
  document.querySelectorAll("[data-submit-once]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.submitting === "true") { event.preventDefault(); return; }
      const button = event.submitter || form.querySelector("button[type='submit']");
      if (!button || button.disabled) return;
      // Disabled submitters are omitted from native form data; preserve their intent.
      if (button.name) {
        const intent = document.createElement("input");
        intent.type = "hidden";
        intent.name = button.name;
        intent.value = button.value;
        form.append(intent);
      }
      form.dataset.submitting = "true";
      form.querySelectorAll("button[type='submit']").forEach((item) => { item.disabled = true; });
      button.textContent = button.dataset.submitLabel || "正在提交";
    });
  });
}

function setupReview() {
  const form = document.querySelector("[data-review-form]");
  if (!form) return;
  const rows = Array.from(form.querySelectorAll("[data-review-row]"));
  const summary = form.querySelector("[data-review-summary]");
  const submit = form.querySelector("[data-review-submit]");
  const filters = Array.from(form.querySelectorAll("[data-review-filter]"));
  const empty = form.querySelector("[data-review-empty]");
  let filter = "all";
  const actionOf = (row) => row.querySelector('select[name$="_action"]');
  const validDate = (value) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  };
  const needsAttention = (row) => {
    const action = actionOf(row).value;
    if (action !== "ignore") {
      // Validate only controls explicitly present on this row; supplemental rows
      // do not necessarily expose the same fields as tasks.
      const project = row.querySelector("[data-review-project]");
      if (project && !project.value) return true;
      const existing = row.querySelector("[data-existing-field] select");
      if (action === "update" && (!existing || !existing.value)) return true;
      const invalidDate = Array.from(row.querySelectorAll("[data-review-date]")).some((field) => {
        const value = field.value.trim();
        return (value || field.dataset.dateNeedsCorrection === "true") && !validDate(value);
      });
      if (invalidDate) return true;
    }
    return row.dataset.needsAttention === "true" && row.dataset.reviewed !== "true";
  };
  const labels = {create: "新建", update: "更新", ignore: "忽略"};
  const sync = () => {
    const counts = {create: 0, update: 0, ignore: 0, attention: 0};
    rows.forEach((row) => {
      const action = actionOf(row);
      if (!action) return;
      counts[action.value] += 1;
      if (needsAttention(row)) counts.attention += 1;
      row.hidden = filter === "attention" && !needsAttention(row);
      const label = row.querySelector("[data-action-label]");
      if (label) label.textContent = labels[action.value];
    });
    summary.textContent = `预计新增 ${counts.create} 项 · 更新 ${counts.update} 项 · 忽略 ${counts.ignore} 项 · 需确认 ${counts.attention} 项`;
    submit.textContent = `确认并入库（新增 ${counts.create}，更新 ${counts.update}，忽略 ${counts.ignore}）`;
    filters.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.reviewFilter === filter)));
    empty.hidden = filter !== "attention" || counts.attention > 0;
  };
  filters.forEach((button) => button.addEventListener("click", () => {
    filter = button.dataset.reviewFilter;
    sync();
  }));
  // A decision acknowledges matching warnings, but cannot bypass invalid fields.
  const onEdit = (event) => {
    const row = event.target.closest("[data-review-row]");
    if (!row) return;
    if (event.target === actionOf(row) || actionOf(row).value !== "ignore") row.dataset.reviewed = "true";
    sync();
  };
  form.addEventListener("change", onEdit);
  form.addEventListener("input", onEdit);
  form.querySelectorAll("[data-batch-action]").forEach((button) => button.addEventListener("click", () => {
    rows.forEach((row) => {
      const action = actionOf(row);
      if (button.dataset.batchAction === "recommended") {
        const recommendation = row.getAttribute("data-recommended-action");
        if (!recommendation) return;
        action.value = recommendation;
        const existing = row.querySelector("[data-existing-field] select");
        if (recommendation === "update" && existing) existing.value = row.dataset.recommendedExisting;
      } else {
        if (!needsAttention(row)) return;
        action.value = "ignore";
      }
      row.dataset.reviewed = "true";
      action.dispatchEvent(new Event("change", {bubbles: true}));
    });
    sync();
  }));
  // Reveal invalid controls even when their details or filter was collapsed.
  form.addEventListener("invalid", (event) => {
    const row = event.target.closest("[data-review-row]");
    if (row) { filter = "all"; row.open = true; sync(); }
  }, true);
  sync();
}

function setupCopy() {
  const status = document.querySelector("[data-copy-status]");
  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copyTarget);
      if (!target) return;
      status.textContent = "正在复制…";
      try {
        await navigator.clipboard.writeText(target.value ?? target.textContent);
        status.textContent = "已复制，可以粘贴到钉钉。";
      } catch (error) {
        status.textContent = "复制失败，请选中文案后手动复制。";
      }
    });
  });
}

function setupReportTabs() {
  const tabs = Array.from(document.querySelectorAll("[data-report-tab]"));
  const select = (active, focus = false) => tabs.forEach((tab) => {
    const selected = tab === active;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    document.getElementById(tab.getAttribute("aria-controls")).hidden = !selected;
    if (selected && focus) tab.focus();
  });
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (event) => {
      let next;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      else return;
      event.preventDefault();
      select(tabs[next], true);
    });
  });
  if (tabs.length) select(tabs[0]);
}

function setupShortcuts() {
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey ||
        target.isContentEditable || target.closest("input, textarea, select, [role='textbox']")) return;
    if (event.key === "/") {
      const search = document.querySelector("[data-search-input]");
      if (search) { event.preventDefault(); search.focus(); }
    } else if (event.key === "n") {
      const link = document.querySelector("[data-shortcut-new-meeting]");
      if (link) { event.preventDefault(); window.location.assign(link.href); }
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupParseForm();
  setupTaskActions();
  setupReview();
  setupCopy();
  setupReportTabs();
  setupShortcuts();
  setupSubmitOnce();
});
