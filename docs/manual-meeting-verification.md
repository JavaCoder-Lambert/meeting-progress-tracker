# 手动会议工作台：交付与验证

日期：2026-09-06

## 已交付

手动设置会议主题、日期、开始时间、议题、项目和汇报人员；按人选择已有任务或新建任务，记录进展、下一步、风险、待决策和普通纪要。编辑任务进展会自动标记已询问，也可主动取消。原地补建项目/人员，会中服务端暂存，会后预览并一次确认，生成可复制纪要。原有粘贴 / AI 入口保留。

采用紧凑双栏、渐进展开的选填内容及手机固定底部操作；没有增加前端框架、CDN、数据库服务或模型调用。列表与首页不加载整份会议大文本。

历史会议仅补记已有任务历史，不覆盖最新任务。签名基线、草稿版本和事务防止旧标签覆盖、重复确认。业务日期与录入日期分开，周报包含无文字说明的快捷完成操作。

## 检查记录

- `UV_CACHE_DIR=/tmp/meeting-progress-uv-cache uv run pytest -q`：253 passed。
- `node --test tests/js/*.test.cjs`：68 passed。
- `node --check static/js/meeting-workspace.js`：通过。
- `uv run python manage.py check`：无问题。
- `uv run python manage.py makemigrations --check --dry-run`：No changes detected。
- `git diff --check`：通过。
- 独立测试数据库 + 实际 Chrome：新建会议、已有任务、编辑自动标记已询问、切人、刷新恢复、补建人员、新任务、风险/纪要、断网阻止退出及重试、多标签冲突保护、预览、确认失败返回重试、最终只读纪要/复制全部通过，无 JS 页面错误。
- 1440px 桌面与 390px 手机截图已检查；无横向溢出，长页面仍可使用固定底部操作。
- 容器构建通过；本地 app / worker 健康；增量迁移 `0005_manual_meetings` 已应用；SQLite `integrity_check` 为 `ok`。
- 升级前冻结写入并备份；升级后原会议、任务、项目、人员条数一致；真实服务未创建演示业务记录，未发送测试数据给大模型。
- 真实本地服务登录及 9 个页面 GET 通过；新页面 CSS/JS 使用内容哈希、gzip 与 immutable 缓存。
- `.env`、`.idea`、数据库和本地评审目录仍被 Git 忽略，未纳入提交。

Docker 本机缺少 buildx 插件会显示警告，本次使用兼容构建器成功完成；不影响运行。未更改服务器部署模板、数据卷和 env 注入方式，也未部署任何远程服务器。

## 修改文件清单

### 领域与 HTTP

- `core/models.py`
- `core/migrations/0005_manual_meetings.py`
- `core/manual_meeting_forms.py`
- `core/manual_meeting_views.py`
- `core/services/manual_meetings.py`
- `core/services/manual_meeting_state.py`
- `core/services/manual_meeting_preview.py`
- `core/services/dashboard.py`
- `core/services/exports.py`
- `core/services/reports.py`
- `core/urls.py`
- `core/views.py`

### 页面与交互

- `static/css/meeting-workspace.css`
- `static/js/meeting-workspace.js`
- `templates/base.html`
- `templates/core/manual_meeting_form.html`
- `templates/core/manual_meeting_workspace.html`
- `templates/core/manual_meeting_preview.html`
- `templates/core/meeting_form.html`
- `templates/core/meeting_list.html`
- `templates/core/dashboard.html`
- `templates/core/task_detail.html`

### 测试与说明

- `tests/test_manual_meetings.py`
- `tests/test_manual_meeting_views.py`
- `tests/test_reports_and_exports.py`
- `tests/js/meeting-workspace.test.cjs`
- `README.md`
- `docs/superpowers/specs/2026-09-06-manual-meeting-workspace.md`
- `docs/superpowers/plans/2026-09-06-manual-meeting-workspace.md`
- `docs/manual-meeting-verification.md`

## 当前边界

- 会中未到达服务器的编辑，强制关闭浏览器或断电仍可能丢失；应看到已暂存后离开。版本冲突可下载本页草稿保全内容，不能直接覆盖新版。
- 已确认会议只读，不含撤销重开和手动 / AI 自动混合合并。
- 未来会议可提前安排，不能提前确认进度。
- 数据迁移使用完整 SQLite 备份，env 单独安全复制；JSON/CSV 导出不替代完整备份恢复。
