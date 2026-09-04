# Meeting Progress Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-user, Dockerized Django application that turns pasted meeting notes into reviewable AI drafts, tracked project work, and Markdown weekly reports.

**Architecture:** A server-rendered Django monolith uses HTMX for focused interactions, SQLite in `/data` for persistence, and a small application-service layer for LLM parsing, draft confirmation, reporting, and exports. The browser never receives model credentials; parsed data becomes formal records only inside an explicit transactional confirmation operation.

**Tech Stack:** Python 3.12, Django 5.2 LTS, HTMX, Tailwind standalone output, Pydantic 2, HTTPX, pytest, pytest-django, Gunicorn, SQLite, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-04-meeting-progress-tracker-design.md`

## Global Constraints

- Single administrator account; people assigned to tasks are not login users.
- Desktop-first UI and Chinese interface copy.
- AI only creates an editable draft and never directly mutates formal records.
- SQLite runs in WAL mode with one Gunicorn worker and persists below `/data`.
- LLM access uses `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, and `LLM_TIMEOUT_SECONDS`.
- No DingTalk API, notifications, multi-user permissions, attachments, complex Gantt, or Feishu sync in v1.
- Every production behavior is introduced through a failing test first.

---

### Task 1: Runnable Django foundation and administrator bootstrap

**Files:**
- Create: `pyproject.toml`
- Create: `manage.py`
- Create: `tracker/__init__.py`
- Create: `tracker/settings.py`
- Create: `tracker/urls.py`
- Create: `tracker/wsgi.py`
- Create: `core/__init__.py`
- Create: `core/apps.py`
- Create: `core/views.py`
- Create: `core/urls.py`
- Create: `core/management/commands/bootstrap_admin.py`
- Create: `templates/base.html`
- Create: `templates/registration/login.html`
- Create: `templates/core/dashboard.html`
- Create: `tests/conftest.py`
- Create: `tests/test_bootstrap_and_access.py`

**Interfaces:**
- Produces: Django project, `/health/`, authenticated `/`, and `manage.py bootstrap_admin`.
- Consumes: environment variables named in Global Constraints.

- [ ] **Step 1: Write failing access and bootstrap tests**

```python
def test_dashboard_redirects_anonymous(client):
    response = client.get("/")
    assert response.status_code == 302
    assert response.url.startswith("/accounts/login/")

def test_health_is_public(client):
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_bootstrap_admin_does_not_replace_existing_password(settings, monkeypatch, django_user_model):
    monkeypatch.setenv("ADMIN_USERNAME", "owner")
    monkeypatch.setenv("ADMIN_PASSWORD", "first-secret")
    call_command("bootstrap_admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "replacement")
    call_command("bootstrap_admin")
    user = django_user_model.objects.get(username="owner")
    assert user.check_password("first-secret")
    assert not user.check_password("replacement")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_bootstrap_and_access.py -q`
Expected: FAIL because the Django project and command do not exist.

- [ ] **Step 3: Implement the minimal project, auth pages, health view, and idempotent bootstrap command**

The bootstrap command must create a superuser only when no superuser exists, require non-empty environment credentials, and leave existing passwords unchanged.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest tests/test_bootstrap_and_access.py -q`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml manage.py tracker core templates tests
git commit -m "feat: add authenticated Django foundation"
```

### Task 2: Domain models, migrations, and admin management

**Files:**
- Create: `core/models.py`
- Create: `core/admin.py`
- Create: `core/migrations/0001_initial.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Produces: `Project`, `Person`, `MeetingNote`, `ImportDraft`, `Task`, `ProgressUpdate`, `Risk`, and `Milestone` ORM models with enum choices from the spec.
- Consumes: Django settings and authenticated administrator.

- [ ] **Step 1: Write failing model behavior tests**

```python
def test_task_progress_is_limited_to_percentage(project):
    task = Task(project=project, title="接口联调", progress=101)
    with pytest.raises(ValidationError):
        task.full_clean()

def test_task_is_overdue_only_when_open_and_past_due(project):
    task = Task(project=project, title="接口联调", due_date=date.today() - timedelta(days=1))
    assert task.is_overdue is True
    task.status = Task.Status.DONE
    assert task.is_overdue is False

def test_project_and_person_names_are_unique(db):
    Project.objects.create(name="金蝶")
    with pytest.raises(IntegrityError):
        Project.objects.create(name="金蝶")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_models.py -q`
Expected: FAIL because domain models do not exist.

- [ ] **Step 3: Implement focused models, constraints, indexes, string representations, properties, migration, and admin registration**

Use database check constraints for progress percentages and uniqueness for project/person names. Index task status, due date, project, and assignee; index meeting date and unresolved risk status.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest tests/test_models.py -q && python manage.py makemigrations --check --dry-run`
Expected: all tests PASS and no model changes detected.

- [ ] **Step 5: Commit**

```bash
git add core/models.py core/admin.py core/migrations tests/test_models.py
git commit -m "feat: add project tracking domain models"
```

### Task 3: LLM schema, normalization, and task matching

**Files:**
- Create: `core/services/__init__.py`
- Create: `core/services/llm_schema.py`
- Create: `core/services/llm_client.py`
- Create: `core/services/task_matching.py`
- Create: `tests/test_llm_schema.py`
- Create: `tests/test_task_matching.py`

**Interfaces:**
- Produces: `parse_meeting_note(note: MeetingNote) -> ImportDraft`, `ParsedMeeting`, `normalize_date(value, meeting_date)`, and `find_task_candidates(item, queryset)`.
- Consumes: OpenAI-compatible Chat Completions endpoint and active domain context.

- [ ] **Step 1: Write failing schema and matching tests**

```python
def test_yearless_date_uses_meeting_year():
    assert normalize_date("9月16日", date(2026, 8, 20)) == date(2026, 9, 16)

def test_invalid_progress_is_rejected():
    with pytest.raises(ValidationError):
        ParsedTask(title="联调", progress=120)

def test_candidate_match_does_not_update_task(project, person):
    existing = Task.objects.create(project=project, assignee=person, title="发货仓库优先级逻辑调整")
    candidates = find_task_candidates(
        {"project_name": project.name, "assignee_name": person.name, "title": "完成发货仓库优先级逻辑调整"},
        Task.objects.all(),
    )
    existing.refresh_from_db()
    assert candidates[0].task_id == existing.id
    assert existing.title == "发货仓库优先级逻辑调整"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_llm_schema.py tests/test_task_matching.py -q`
Expected: FAIL because service modules do not exist.

- [ ] **Step 3: Implement strict Pydantic schemas, Chinese date normalization, deterministic candidate scoring, safe prompt construction, HTTPX call, JSON extraction, and user-facing error types**

The client sends only active projects, people, incomplete task summaries, and current note text. It saves raw response on the note, stores validated payload in a new draft, and converts network, HTTP, empty-response, and schema errors into Chinese messages without logging secrets.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest tests/test_llm_schema.py tests/test_task_matching.py -q`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/services tests/test_llm_schema.py tests/test_task_matching.py
git commit -m "feat: parse meeting notes into validated AI drafts"
```

### Task 4: Transactional draft confirmation

**Files:**
- Create: `core/services/draft_confirmation.py`
- Create: `tests/test_draft_confirmation.py`

**Interfaces:**
- Produces: `confirm_draft(draft_id: int, decisions: dict) -> ConfirmationResult`.
- Consumes: validated draft payload and explicit per-item decisions `create`, `update`, or `ignore`.

- [ ] **Step 1: Write failing confirmation tests**

```python
def test_confirm_creates_records_and_progress_history(parsed_draft, project, person):
    result = confirm_draft(parsed_draft.id, decisions_for_create(project, person))
    task = Task.objects.get(title="发货仓库优先级逻辑调整")
    assert result.created_tasks == 1
    assert task.progress_updates.count() == 1
    parsed_draft.refresh_from_db()
    assert parsed_draft.confirmed_at is not None

def test_invalid_item_rolls_back_entire_confirmation(parsed_draft, project):
    decisions = decisions_with_one_invalid_task(parsed_draft, project)
    with pytest.raises(DraftConfirmationError):
        confirm_draft(parsed_draft.id, decisions)
    assert Task.objects.count() == 0
    parsed_draft.refresh_from_db()
    assert parsed_draft.confirmed_at is None

def test_confirmed_draft_cannot_be_confirmed_twice(parsed_draft):
    confirm_draft(parsed_draft.id, valid_decisions(parsed_draft))
    with pytest.raises(DraftAlreadyConfirmed):
        confirm_draft(parsed_draft.id, valid_decisions(parsed_draft))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_draft_confirmation.py -q`
Expected: FAIL because confirmation service does not exist.

- [ ] **Step 3: Implement atomic confirmation with row locking, full validation before saves, create/update/ignore decisions, progress snapshots, and duplicate confirmation protection**

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest tests/test_draft_confirmation.py -q`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/services/draft_confirmation.py tests/test_draft_confirmation.py
git commit -m "feat: confirm AI drafts transactionally"
```

### Task 5: Meeting capture and draft-review workflow

**Files:**
- Create: `core/forms.py`
- Modify: `core/views.py`
- Modify: `core/urls.py`
- Create: `templates/core/meeting_form.html`
- Create: `templates/core/meeting_detail.html`
- Create: `templates/core/draft_review.html`
- Create: `templates/core/partials/parse_status.html`
- Create: `tests/test_meeting_workflow.py`

**Interfaces:**
- Produces: meeting create/detail, parse action, editable review, and confirm endpoints.
- Consumes: `parse_meeting_note` and `confirm_draft` services.

- [ ] **Step 1: Write failing HTTP workflow tests**

```python
def test_authenticated_user_can_save_raw_meeting(admin_client):
    response = admin_client.post("/meetings/new/", {"title": "周五进度会", "meeting_date": "2026-09-04", "raw_text": "张川：数据迁移完成"})
    assert response.status_code == 302
    assert MeetingNote.objects.get().raw_text == "张川：数据迁移完成"

def test_confirm_validation_error_writes_nothing(admin_client, draft):
    response = admin_client.post(f"/drafts/{draft.id}/confirm/", invalid_confirmation_form())
    assert response.status_code == 200
    assert "请修正" in response.content.decode()
    assert Task.objects.count() == 0
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_meeting_workflow.py -q`
Expected: FAIL because routes and templates do not exist.

- [ ] **Step 3: Implement forms, protected views, status transitions, Chinese validation messages, dual-column review template, and PRG redirects after successful writes**

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest tests/test_meeting_workflow.py -q`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/forms.py core/views.py core/urls.py templates/core tests/test_meeting_workflow.py
git commit -m "feat: add meeting capture and review workflow"
```

### Task 6: Dashboard, tracking views, reports, and exports

**Files:**
- Create: `core/services/dashboard.py`
- Create: `core/services/reports.py`
- Create: `core/services/exports.py`
- Modify: `core/forms.py`
- Modify: `core/views.py`
- Modify: `core/urls.py`
- Modify: `templates/core/dashboard.html`
- Create: `templates/core/task_list.html`
- Create: `templates/core/task_board.html`
- Create: `templates/core/task_detail.html`
- Create: `templates/core/project_list.html`
- Create: `templates/core/project_detail.html`
- Create: `templates/core/person_list.html`
- Create: `templates/core/person_detail.html`
- Create: `templates/core/report.html`
- Create: `templates/core/settings.html`
- Create: `tests/test_dashboard_and_filters.py`
- Create: `tests/test_reports_and_exports.py`

**Interfaces:**
- Produces: dashboard metrics, filtered task table/board, project/person detail pages, `build_weekly_report(start, end)`, and secret-free JSON/CSV ZIP exports.
- Consumes: formal domain records only; reports never modify data.

- [ ] **Step 1: Write failing dashboard, filtering, report, and export tests**

```python
def test_task_filter_combines_project_status_and_assignee(admin_client, project, person):
    wanted = Task.objects.create(project=project, assignee=person, title="联调", status=Task.Status.IN_PROGRESS)
    response = admin_client.get("/tasks/", {"project": project.id, "assignee": person.id, "status": Task.Status.IN_PROGRESS})
    assert response.status_code == 200
    assert list(response.context["tasks"]) == [wanted]

def test_weekly_report_uses_updates_in_selected_range(task):
    ProgressUpdate.objects.create(task=task, new_status=Task.Status.DONE, completed_work="接口完成", recorded_at=aware_datetime(2026, 9, 3))
    report = build_weekly_report(date(2026, 8, 31), date(2026, 9, 6))
    assert "接口完成" in report.markdown

def test_json_export_has_no_credentials(admin_client, settings):
    settings.LLM_API_KEY = "never-export-this"
    response = admin_client.get("/settings/export/json/")
    assert response.status_code == 200
    assert b"never-export-this" not in response.content
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_dashboard_and_filters.py tests/test_reports_and_exports.py -q`
Expected: FAIL because query/report/export services and pages do not exist.

- [ ] **Step 3: Implement query services, forms, views, responsive desktop templates, Markdown report generation, clipboard action, JSON export, and per-entity CSV ZIP export**

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest tests/test_dashboard_and_filters.py tests/test_reports_and_exports.py -q`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core templates/core tests/test_dashboard_and_filters.py tests/test_reports_and_exports.py
git commit -m "feat: add project tracking dashboards and reports"
```

### Task 7: Production container, documentation, and full verification

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Create: `.env.example`
- Create: `docker/entrypoint.sh`
- Create: `README.md`
- Create: `static/css/app.css`
- Create: `tests/test_security_settings.py`
- Modify: `tracker/settings.py`

**Interfaces:**
- Produces: one-service production Compose deployment with health check and `/data` volume.
- Consumes: environment configuration and all prior application components.

- [ ] **Step 1: Write failing production configuration tests**

```python
def test_production_settings_require_secret_key(monkeypatch):
    monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
    monkeypatch.setenv("DJANGO_DEBUG", "false")
    result = subprocess.run([sys.executable, "-c", "import tracker.settings"], text=True, capture_output=True)
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr

def test_database_path_uses_data_dir(settings):
    assert settings.DATABASES["default"]["NAME"] == settings.DATA_DIR / "app.sqlite3"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_security_settings.py -q`
Expected: FAIL until production settings are enforced.

- [ ] **Step 3: Implement environment parsing, SQLite WAL initialization, static serving/build, entrypoint migrations/bootstrap, non-root image, Compose volume/health check, and Chinese deployment/backup README**

The entrypoint executes migrations and `bootstrap_admin`, then starts Gunicorn with one worker. README includes local development, model configuration, first deployment, Nginx/Caddy proxy expectations, upgrade, stop-and-copy backup, restore, and key rotation.

- [ ] **Step 4: Run automated verification**

Run: `pytest -q`
Expected: all tests PASS.

Run: `python manage.py check --deploy`
Expected: no unaddressed production security warnings under documented production environment variables.

Run: `python manage.py makemigrations --check --dry-run`
Expected: no model changes detected.

- [ ] **Step 5: Run container verification**

Run: `docker compose config && docker compose build && docker compose up -d --wait`
Expected: configuration valid, image builds, application becomes healthy.

Create a project through the browser, restart with `docker compose restart`, and verify the project remains visible.

- [ ] **Step 6: Run browser acceptance flow**

Verify login, raw-note save, model parse or deterministic test endpoint fixture, dual-column review, manual correction, transactional confirmation, task filtering, project/person views, weekly report copy/download, JSON export, CSV ZIP export, password change, and logout.

- [ ] **Step 7: Run final repository checks and commit**

Run: `git diff --check && git status --short`
Expected: no whitespace errors; only intended files are present.

```bash
git add Dockerfile docker-compose.yml .dockerignore .env.example docker README.md static tracker/settings.py tests/test_security_settings.py
git commit -m "feat: ship containerized meeting progress tracker"
```
