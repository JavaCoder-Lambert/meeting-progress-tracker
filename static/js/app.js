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
  const originalLabel = buttonLabel.textContent;
  let running = false;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (running) return;
    running = true;
    button.disabled = true;
    button.classList.add("is-loading");
    buttonLabel.textContent = "解析中";
    progress.hidden = false;
    progress.classList.remove("is-error");
    progressTitle.textContent = "正在整理会议内容";
    progressDetail.textContent = "复杂记录通常需要 20–90 秒，请不要重复点击或关闭页面。";

    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      elapsed.textContent = `已等待 ${Math.floor((Date.now() - startedAt) / 1000)} 秒`;
    }, 1000);

    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
      });
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        throw new Error(`服务器返回异常（HTTP ${response.status}），请刷新后重试。`);
      }
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || "解析未完成，请稍后重试。");
      progressTitle.textContent = "解析完成，正在打开草稿";
      progressDetail.textContent = "你可以在下一页核对任务、负责人和日期。";
      window.location.assign(data.redirect_url);
    } catch (error) {
      if (statusBadge) {
        statusBadge.className = "status-badge status-failed";
        statusBadge.innerHTML = '<span class="status-dot" aria-hidden="true"></span>解析失败';
      }
      progress.classList.add("is-error");
      progressTitle.textContent = "解析未完成";
      progressDetail.textContent = error.message || "网络连接异常，请稍后重试。";
      button.disabled = false;
      button.classList.remove("is-loading");
      buttonLabel.textContent = originalLabel;
      running = false;
    } finally {
      window.clearInterval(timer);
    }
  });
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
    form.addEventListener("submit", () => {
      const button = form.querySelector("button[type='submit']");
      if (!button || button.disabled) return;
      button.disabled = true;
      button.textContent = button.dataset.submitLabel || "正在提交";
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupParseForm();
  setupTaskActions();
  setupSubmitOnce();
});
