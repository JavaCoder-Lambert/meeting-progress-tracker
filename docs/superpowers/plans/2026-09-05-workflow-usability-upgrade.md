# Workflow Usability Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the solo meeting workflow faster and clearer from capture through AI review, task progress, dashboard follow-up, and weekly reporting.

**Architecture:** Reuse the existing models and add focused presentation/services around them. Server-rendered Django owns all durable behavior; progressive enhancement in one JavaScript file provides filters, bulk review actions, copying, and keyboard shortcuts.

**Tech Stack:** Python 3.12, Django 5.2, Django Templates, SQLite, native CSS and JavaScript, pytest-django.

**Spec:** `docs/superpowers/specs/2026-09-05-workflow-usability-upgrade-design.md`

## Global Constraints

- Keep SQLite, Django Templates, native CSS and native JavaScript; add no external service, queue, frontend framework, or database model.
- AI output remains a draft until explicit confirmation.
- Missing project mappings must never default to creating formal tasks.
- All durable behavior is usable without JavaScript; JavaScript only reduces steps.
- Preserve login protection, transaction atomicity, Docker deployment, WCAG AA contrast, keyboard focus, reduced motion, and narrow-screen support.

---

### Task 1: Exception-first draft review

**Files:**
- Create: `core/services/draft_review.py`
- Modify: `core/views.py`
- Modify: `core/services/draft_confirmation.py`
- Modify: `templates/core/draft_review.html`
- Test: `tests/test_draft_review.py`
- Test: `tests/test_draft_confirmation.py`

**Interfaces:**
- Consumes: `find_task_candidates(item, queryset)`, existing `ImportDraft.payload`.
- Produces: `build_draft_review(draft, decisions=None) -> dict` with `task_rows`, `risk_rows`, `milestone_rows`, and `review_counts`; each task row exposes `recommended_action`, `action`, `existing_id`, `attention_reasons`, `needs_attention`, and `diffs`.

- [ ] Write tests proving unmatched projects default to ignore, exact unique candidates recommend update, safe mapped tasks recommend create, only one open-task query is evaluated, and review counts are correct.
- [ ] Run `UV_CACHE_DIR=/tmp/meeting-progress-uv-cache uv run pytest tests/test_draft_review.py tests/test_draft_confirmation.py -q` and verify the new tests fail for missing behavior.
- [ ] Implement `build_draft_review`, reuse one materialized open-task collection, and move `_date_input`/review context construction out of `views.py`.
- [ ] Add uniform task/risk/milestone decision-length validation before writes in `confirm_draft`.
- [ ] Replace the expanded card list with compact `details` rows, attention reasons, differences, filters, batch controls, and an import summary while preserving every existing form field name.
- [ ] Re-run the targeted tests and verify they pass.

### Task 2: Task progress detail and weekly-report loop

**Files:**
- Create: `core/services/progress_updates.py`
- Create: `templates/core/task_detail.html`
- Modify: `core/forms.py`
- Modify: `core/views.py`
- Modify: `core/urls.py`
- Modify: `templates/core/task_list.html`
- Test: `tests/test_task_progress_updates.py`
- Test: `tests/test_reports_and_exports.py`

**Interfaces:**
- Consumes: `Task`, `ProgressUpdate`, `build_weekly_report`.
- Produces: `TaskProgressForm`; `record_task_progress(task, cleaned_data) -> ProgressUpdate`; routes `task_detail` and `task_progress_update`.

- [ ] Write tests proving text-only updates create history, previous/new values are correct, done/reopened states update `completed_at`, invalid forms write nothing, and the update appears in weekly report output.
- [ ] Run the new targeted tests and verify they fail for missing routes/service.
- [ ] Implement the transactional progress service and focused form with Chinese labels and compact widgets.
- [ ] Add the task detail/update views and routes; keep full task editing as a secondary action.
- [ ] Render task summary, progress form, source meeting, related risks, and a reverse chronological timeline; point task-list rows to details.
- [ ] Re-run the targeted tests and verify they pass.

### Task 3: Meeting inbox, capture shortcut, and action dashboard

**Files:**
- Modify: `core/services/dashboard.py`
- Modify: `core/forms.py`
- Modify: `core/views.py`
- Modify: `core/urls.py`
- Modify: `templates/base.html`
- Modify: `templates/core/dashboard.html`
- Modify: `templates/core/meeting_form.html`
- Modify: `templates/core/meeting_detail.html`
- Create: `templates/core/meeting_list.html`
- Test: `tests/test_meeting_inbox.py`
- Test: `tests/test_dashboard_and_filters.py`

**Interfaces:**
- Consumes: current meeting/draft/task/risk records.
- Produces: `meeting_list`; dashboard keys `pending_drafts`, `overdue_tasks`, `due_soon_tasks`, `stale_tasks`, `open_risks`, `follow_up_groups`, and `action_counts`.

- [ ] Write tests for meeting-state actions, latest-draft selection, save-and-parse redirect, mutually exclusive deadline buckets, completed-task exclusion, stale-task selection, and per-person follow-up text.
- [ ] Run the targeted tests and verify the new tests fail for missing behavior.
- [ ] Implement the meeting inbox and latest-draft selection with `Prefetch` or a subquery so old drafts never become the continue action.
- [ ] Add `intent=parse` capture behavior, redirecting to the meeting detail with an auto-parse flag while keeping the non-JavaScript parse button.
- [ ] Refactor dashboard context into action-oriented querysets and deterministic DingTalk-ready follow-up strings.
- [ ] Render clickable action counts, prioritized queues, useful empty states, and meeting status actions.
- [ ] Re-run targeted tests and verify they pass.

### Task 4: Cohesive visual system and progressive interactions

**Files:**
- Create: `DESIGN.md`
- Modify: `static/css/app.css`
- Modify: `static/js/app.js`
- Modify: `templates/base.html`
- Modify: `templates/core/dashboard.html`
- Modify: `templates/core/draft_review.html`
- Modify: `templates/core/task_list.html`
- Modify: `templates/core/task_board.html`
- Modify: `templates/core/project_list.html`
- Modify: `templates/core/project_detail.html`
- Modify: `templates/core/person_detail.html`
- Modify: `templates/core/report.html`
- Test: `tests/test_frontend_ui.py`

**Interfaces:**
- Consumes: semantic classes/data attributes from Tasks 1-3.
- Produces: reusable `surface`, `data-list`, `data-row`, `summary-strip`, `filter-chip`, `empty-state`, `sticky-action-bar`, copy feedback, review filtering/bulk actions, report preview, and keyboard shortcuts.

- [ ] Write UI contract tests for skip navigation, accessible progress bars/tables, copy live status, review filters and summary, report tabs, mobile overflow protection, and keyboard shortcut hooks.
- [ ] Run `UV_CACHE_DIR=/tmp/meeting-progress-uv-cache uv run pytest tests/test_frontend_ui.py -q` and verify new assertions fail.
- [ ] Replace fluid product headings and repetitive floating cards with a fixed type scale, restrained surfaces, compact data rows, visible focus, semantic state colors, and responsive layouts.
- [ ] Implement review filtering/bulk decisions, live create/update/ignore counts, generic copy feedback, report preview tabs, `/` search focus and `n` meeting shortcut.
- [ ] Improve board density, task filtering, project/person pages, form labels, empty states, table scopes, time elements, and progressbar semantics.
- [ ] Write `DESIGN.md` from the implemented token/component system and re-run UI tests.

### Task 5: Integration and deployment verification

**Files:**
- Modify: `README.md`
- Modify: only files required by verification defects.

**Interfaces:**
- Consumes: all prior task outputs.
- Produces: verified local/Docker application and deployment guidance.

- [ ] Run the full test suite, `manage.py check`, migration drift check, and `git diff --check`.
- [ ] Build and start Docker Compose, verify `/health/`, authenticated desktop flow, 390px layout, browser console, review bulk controls, task progress submission, and report copying.
- [ ] Update README with the meeting inbox, progress-update workflow, keyboard shortcuts, and the truthful limitation that model generation remains synchronous.
- [ ] Fix only defects discovered by these checks, rerun the affected check, and record the exact final commands/results.
