import json
from datetime import date

import httpx
import pytest
from django.utils import timezone

from core.models import ImportDraft, MeetingNote, Milestone, ProgressUpdate, Project, Risk, Task
from core.services.draft_confirmation import DraftConfirmationError, confirm_draft
from core.services.ai_history import task_baseline
from core.services.parse_jobs import run_next_job


def make_note(**kwargs):
    return MeetingNote.objects.create(
        title="终审会议", meeting_date=kwargs.pop("meeting_date", date(2026, 9, 5)), raw_text="下一步安排验收", **kwargs,
    )


def parse_task(admin_client, monkeypatch, settings, project, **fields):
    settings.LLM_API_KEY = "offline-test"
    settings.LLM_MODEL = "offline-test"

    async def post(*args, **kwargs):
        content = json.dumps({"tasks": [{"title": "接口联调", "project_name": project.name,
                                         "next_step": "安排验收", **fields}]})
        return httpx.Response(200, request=httpx.Request("POST", "https://example.test"),
                              json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr("httpx.AsyncClient.post", post)
    note = make_note(meeting_date=timezone.localdate())
    response = admin_client.post(f"/meetings/{note.pk}/parse/")
    assert response.status_code == 302
    assert run_next_job() is True
    return note.drafts.get()


def review_post(admin_client, draft):
    response = admin_client.get(f"/drafts/{draft.pk}/")
    row = response.context["task_rows"][0]
    data = {"task_0_action": row["action"], "task_0_project": row["project_id"],
            "task_0_existing": row["existing_id"]}
    for field in ("title", "status", "priority", "progress", "next_step"):
        data[f"task_0_{field}"] = row["item"].get(field, "")
    if response.context.get("review_baseline"):
        data["review_baseline"] = response.context["review_baseline"]
    return row, data


@pytest.mark.django_db
def test_model_omissions_preserve_existing_task_through_review_and_http_confirm(admin_client, monkeypatch, settings):
    project = Project.objects.create(name="仓配")
    task = Task.objects.create(project=project, title="接口联调", status="in_progress", progress=80, priority="high")
    draft = parse_task(admin_client, monkeypatch, settings, project)
    row, data = review_post(admin_client, draft)
    assert row["action"] == "update"
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", data)
    task.refresh_from_db()
    assert response.status_code == 302
    assert (task.status, task.progress, task.priority) == ("in_progress", 80, "high")
    assert (row["item"]["status"], row["item"]["progress"], row["item"]["priority"]) == ("in_progress", 80, "high")
    assert not {"status", "progress", "priority"} & {diff["field"] for diff in row["diffs"]}
    assert task.progress_updates.get().next_step == "安排验收"


@pytest.mark.django_db
@pytest.mark.parametrize("fields, expected", [
    ({"status": "blocked", "progress": 90, "priority": "urgent"}, ("blocked", 90, "urgent")),
    ({"status": "not_started", "progress": 0, "priority": "normal"}, ("not_started", 0, "normal")),
    ({"status": "done"}, ("done", 100, "high")),
])
def test_explicit_model_values_including_defaults_update_existing_task(admin_client, monkeypatch, settings, fields, expected):
    project = Project.objects.create(name="仓配")
    task = Task.objects.create(project=project, title="接口联调", status="in_progress", progress=80, priority="high")
    draft = parse_task(admin_client, monkeypatch, settings, project, **fields)
    _, data = review_post(admin_client, draft)
    assert admin_client.post(f"/drafts/{draft.pk}/confirm/", data).status_code == 302
    task.refresh_from_db()
    assert (task.status, task.progress, task.priority) == expected


@pytest.mark.django_db
def test_new_task_retains_schema_defaults(admin_client, monkeypatch, settings):
    project = Project.objects.create(name="仓配")
    draft = parse_task(admin_client, monkeypatch, settings, project)
    row, data = review_post(admin_client, draft)
    assert row["action"] == "create"
    assert admin_client.post(f"/drafts/{draft.pk}/confirm/", data).status_code == 302
    task = Task.objects.get()
    assert (task.status, task.progress, task.priority) == ("not_started", 0, "normal")


@pytest.mark.django_db
def test_legacy_defaults_are_preserved_but_explicit_user_edits_apply(admin_client):
    project = Project.objects.create(name="仓配")
    task = Task.objects.create(project=project, title="接口联调", status="in_progress", progress=80, priority="high")
    for edit in (False, True):
        note = make_note(parse_status="success", meeting_date=timezone.localdate())
        draft = ImportDraft.objects.create(meeting_note=note, payload={"tasks": [{
            "title": task.title, "project_name": project.name,
            "status": "not_started", "progress": 0, "priority": "normal",
        }]})
        _, data = review_post(admin_client, draft)
        if edit:
            data.update(task_0_status="not_started", task_0_progress=0, task_0_priority="normal")
        assert admin_client.post(f"/drafts/{draft.pk}/confirm/", data).status_code == 302
        task.refresh_from_db()
        assert (task.status, task.progress, task.priority) == (
            ("not_started", 0, "normal") if edit else ("in_progress", 80, "high")
        )


@pytest.mark.django_db
def test_error_redisplay_and_switching_update_target_keep_omitted_fields_omitted(admin_client, monkeypatch, settings):
    project = Project.objects.create(name="仓配")
    first = Task.objects.create(project=project, title="接口联调", status="in_progress", progress=80, priority="high")
    other = Task.objects.create(project=project, title="验收", status="blocked", progress=20, priority="urgent")
    draft = parse_task(admin_client, monkeypatch, settings, project)
    _, data = review_post(admin_client, draft)
    data.update(task_0_existing=other.pk, task_0_project="", task_0_priority="normal")
    error = admin_client.post(f"/drafts/{draft.pk}/confirm/", data)
    assert error.status_code == 200
    row = error.context["task_rows"][0]
    data.update({f"task_0_{field}": row["item"][field] for field in ("status", "progress", "priority")})
    if error.context.get("review_baseline"):
        data["review_baseline"] = error.context["review_baseline"]
    data.update(task_0_existing=first.pk, task_0_project=project.pk)
    assert admin_client.post(f"/drafts/{draft.pk}/confirm/", data).status_code == 302
    first.refresh_from_db()
    other.refresh_from_db()
    assert (first.status, first.progress, first.priority) == ("in_progress", 80, "normal")
    assert (other.status, other.progress, other.priority) == ("blocked", 20, "urgent")


@pytest.mark.django_db
@pytest.mark.parametrize("initial_status, new_status", [
    (None, "done"), ("in_progress", "done"), ("done", "done"), ("done", "in_progress"),
])
def test_draft_completion_transitions_set_preserve_and_clear_timestamp(initial_status, new_status):
    from django.utils import timezone

    project = Project.objects.create(name="仓配")
    completed_at = timezone.now() if initial_status == "done" else None
    existing = Task.objects.create(project=project, title="接口联调", status=initial_status,
                                   completed_at=completed_at) if initial_status else None
    draft = ImportDraft.objects.create(meeting_note=make_note(parse_status="success", meeting_date=timezone.localdate()), payload={
        "tasks": [{"title": "接口联调", "status": new_status}],
    })
    confirm_draft(draft.pk, {"tasks": [{"action": "update" if existing else "create",
                                       "project_id": project.pk, "task_id": existing.pk if existing else None,
                                       "task_baseline": task_baseline(existing, draft) if existing else ""}]})
    task = Task.objects.get()
    assert task.status == new_status
    if new_status == "done":
        assert task.completed_at is not None
        if initial_status == "done":
            assert task.completed_at == completed_at
    else:
        assert task.completed_at is None


def draft_with_all_kinds(project, status="success"):
    return ImportDraft.objects.create(meeting_note=make_note(parse_status=status), payload={
        "tasks": [{"title": "接口联调", "project_name": project.name}],
        "risks": [{"content": "环境不可用"}], "milestones": [{"name": "上线"}],
    })


def assert_no_import(draft):
    for model in (Task, Risk, Milestone, ProgressUpdate):
        assert model.objects.count() == 0
    draft.refresh_from_db()
    assert draft.confirmed_at is None


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["task", "risks", "milestones"])
@pytest.mark.parametrize("action", [None, "invalid"])
def test_http_rejects_missing_or_invalid_decisions_for_every_kind_before_writes(admin_client, kind, action):
    project = Project.objects.create(name="仓配")
    draft = draft_with_all_kinds(project)
    data = {f"{name}_0_{field}": value for name in ("task", "risks", "milestones")
            for field, value in (("action", "create"), ("project", project.pk))}
    if action is None:
        del data[f"{kind}_0_action"]
    else:
        data[f"{kind}_0_action"] = action
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", data)
    assert response.status_code == 200
    assert "处理方式" in response.context["error"]
    assert_no_import(draft)


@pytest.mark.django_db
def test_imported_detail_links_to_confirmed_read_only_result_without_parse_form(admin_client):
    from django.utils import timezone

    note = make_note(parse_status="imported")
    draft = ImportDraft.objects.create(meeting_note=note, payload={"tasks": []}, confirmed_at=timezone.now())
    # Historical databases may have a newer unconfirmed draft after import.
    ImportDraft.objects.create(meeting_note=note, payload={"tasks": []})
    html = admin_client.get(f"/meetings/{note.pk}/?auto_parse=1").content.decode()
    assert "查看已入库结果" in html
    assert f'href="/drafts/{draft.pk}/"' in html
    assert "data-parse-form" not in html
    assert "data-auto-parse" not in html
    result = admin_client.get(f"/drafts/{draft.pk}/").content.decode()
    assert "只读" in result
    assert "确认并入库" not in result


@pytest.mark.django_db
@pytest.mark.parametrize("ajax", [False, True])
def test_imported_parse_post_rejected_before_model_or_new_draft(admin_client, monkeypatch, ajax):
    note = make_note(parse_status="imported")

    def forbidden(_note):
        pytest.fail("Imported meeting reached model parser")

    monkeypatch.setattr("core.services.parse_jobs.generate_meeting_payload", forbidden)
    headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if ajax else {}
    response = admin_client.post(f"/meetings/{note.pk}/parse/", follow=not ajax, **headers)
    if ajax:
        assert response.status_code == 422
        assert "已入库" in response.json()["message"]
    else:
        assert response.status_code == 200
        assert "已入库" in response.content.decode()
    note.refresh_from_db()
    assert note.parse_status == "imported"
    assert note.drafts.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["tasks", "risks", "milestones"])
@pytest.mark.parametrize("action", [None, "invalid"])
def test_service_rejects_missing_or_invalid_decisions_for_every_kind(kind, action):
    project = Project.objects.create(name="仓配")
    draft = draft_with_all_kinds(project)
    decisions = {name: [{"action": "create", "project_id": project.pk}] for name in ("tasks", "risks", "milestones")}
    if action is None:
        del decisions[kind][0]["action"]
    else:
        decisions[kind][0]["action"] = action
    with pytest.raises(DraftConfirmationError, match="处理方式"):
        confirm_draft(draft.pk, decisions)
    assert_no_import(draft)


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["not_parsed", "failed", "parsing", "imported"])
def test_stale_draft_cannot_be_confirmed_unless_meeting_is_currently_success(admin_client, status):
    project = Project.objects.create(name="仓配")
    draft = draft_with_all_kinds(project, status=status)
    data = {f"{name}_0_{field}": value for name in ("task", "risks", "milestones")
            for field, value in (("action", "create"), ("project", project.pk))}
    response = admin_client.post(f"/drafts/{draft.pk}/confirm/", data)
    assert response.status_code == 200
    assert "解析成功" in response.context["error"]
    assert_no_import(draft)
    with pytest.raises(DraftConfirmationError, match="解析成功"):
        confirm_draft(draft.pk, {name: [{"action": "create", "project_id": project.pk}]
                                 for name in ("tasks", "risks", "milestones")})
    assert_no_import(draft)


@pytest.mark.django_db
def test_dashboard_pending_link_lists_exactly_current_success_latest_unconfirmed_meetings(admin_client):
    from django.utils import timezone

    for state in ("not_parsed", "parsing", "success", "failed", "imported"):
        note = make_note(parse_status=state)
        ImportDraft.objects.create(meeting_note=note, payload={})
        latest = ImportDraft.objects.create(meeting_note=note, payload={})
        if state == "success":
            expected_note, expected_draft = note, latest
    confirmed = make_note(parse_status="success")
    ImportDraft.objects.create(meeting_note=confirmed, payload={})
    ImportDraft.objects.create(meeting_note=confirmed, payload={}, confirmed_at=timezone.now())
    dashboard = admin_client.get("/")
    assert dashboard.context["action_counts"]["pending_drafts"] == 1
    assert list(dashboard.context["pending_drafts"]) == [expected_draft]
    assert 'href="/meetings/?state=pending"' in dashboard.content.decode()
    inbox = admin_client.get("/meetings/?state=pending")
    assert list(inbox.context["meeting_list"]) == [expected_note]
    assert f'href="/drafts/{expected_draft.pk}/"' in inbox.content.decode()


@pytest.mark.django_db
def test_only_create_post_enqueues_and_reload_query_or_other_session_never_repeats(admin_client, admin_user, settings):
    from django.test import Client
    from core.models import ParseJob

    settings.LLM_API_KEY = "fake"
    settings.LLM_MODEL = "fake"

    client = Client()
    other = make_note()
    forged = admin_client.get(f"/meetings/{other.pk}/?auto_parse=1")
    assert forged.context["auto_parse"] is False
    assert "data-auto-parse" not in forged.content.decode()
    response = admin_client.post("/meetings/new/", {
        "title": "自动解析会议", "meeting_date": "2026-09-05", "raw_text": "原文", "intent": "parse",
    })
    intended = MeetingNote.objects.get(title="自动解析会议")
    assert response.url == f"/meetings/{intended.pk}/"
    assert ParseJob.objects.count() == 1
    assert admin_client.get(f"/meetings/{other.pk}/?auto_parse=1").context["auto_parse"] is False
    client.force_login(admin_user)
    assert client.get(response.url).context["auto_parse"] is False
    first = admin_client.get(response.url)
    assert first.context["auto_parse"] is False
    assert first.context["parse_active"] is True
    assert "data-auto-parse" not in first.content.decode()
    assert "data-parse-form" in first.content.decode()
    second = admin_client.get(response.url)
    assert second.context["auto_parse"] is False
    assert "data-auto-parse" not in second.content.decode()
    assert "data-parse-form" in second.content.decode()
    assert ParseJob.objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("intent, raw_text", [("save", "原文"), ("invalid", "原文"), ("parse", "")])
def test_save_or_invalid_capture_cannot_grant_auto_parse(admin_client, intent, raw_text):
    note = make_note()
    admin_client.post("/meetings/new/", {
        "title": "不触发解析", "meeting_date": "2026-09-05", "raw_text": raw_text, "intent": intent,
    })
    for stored_note in MeetingNote.objects.all():
        response = admin_client.get(f"/meetings/{stored_note.pk}/?auto_parse=1")
        assert response.context["auto_parse"] is False


@pytest.fixture
def historically_confirmed_meeting(db):
    from django.utils import timezone

    note = make_note(parse_status="success")
    confirmed = ImportDraft.objects.create(meeting_note=note, payload={"tasks": []}, confirmed_at=timezone.now())
    newer = ImportDraft.objects.create(meeting_note=note, payload={"tasks": []})
    return note, confirmed, newer


@pytest.mark.django_db
def test_historical_confirmed_draft_controls_detail_and_inbox_actions(admin_client, historically_confirmed_meeting):
    note, confirmed, newer = historically_confirmed_meeting
    html = admin_client.get(f"/meetings/{note.pk}/?auto_parse=1").content.decode()
    assert "查看已入库结果" in html
    assert f'href="/drafts/{confirmed.pk}/"' in html
    assert f'href="/drafts/{newer.pk}/"' not in html
    assert "data-parse-form" not in html
    assert "data-auto-parse" not in html
    inbox = admin_client.get("/meetings/").content.decode()
    assert "查看结果" in inbox
    assert f'href="/drafts/{newer.pk}/"' not in inbox


@pytest.mark.django_db
@pytest.mark.parametrize("page", ["dashboard", "pending_inbox"])
def test_historical_confirmed_meeting_is_excluded_from_both_pending_queues(admin_client, historically_confirmed_meeting, page):
    if page == "dashboard":
        response = admin_client.get("/")
        assert response.context["action_counts"]["pending_drafts"] == 0
        assert list(response.context["pending_drafts"]) == []
    else:
        response = admin_client.get("/meetings/?state=pending")
        assert list(response.context["meeting_list"]) == []


@pytest.mark.django_db
@pytest.mark.parametrize("ajax", [False, True])
def test_historical_confirmation_blocks_parse_before_parser_call(admin_client, monkeypatch, historically_confirmed_meeting, ajax):
    note, confirmed, newer = historically_confirmed_meeting

    def forbidden(_note):
        pytest.fail("Already-confirmed historical meeting reached parser")

    monkeypatch.setattr("core.services.parse_jobs.generate_meeting_payload", forbidden)
    headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if ajax else {}
    response = admin_client.post(f"/meetings/{note.pk}/parse/", follow=not ajax, **headers)
    if ajax:
        assert response.status_code == 422
        assert "已入库" in response.json()["message"]
    else:
        assert response.status_code == 200
        assert response.redirect_chain[-1][0] == f"/meetings/{note.pk}/"
        assert "已入库" in response.content.decode()
    assert note.drafts.count() == 2
    newer.refresh_from_db()
    assert newer.confirmed_at is None
    with pytest.raises(DraftConfirmationError, match="已经入库"):
        confirm_draft(newer.pk, {})
